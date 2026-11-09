"""Runtime boundaries use disposable enrollments; no hooks, models or user state."""
import json
import hashlib
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime.adapters import codex
from runtime import startup


PACKAGE = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe runtime ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.payload = {"hook_event_name": "PreToolUse", "session_id": "session-1",
                        "cwd": str(self.repo), "turn_id": "turn-1", "tool_use_id": "call-1",
                        "tool_name": "apply_patch", "tool_input": {"command": "PRIVATE_PATCH"}}

    def git(self, root, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                               "-c", "commit.gpgsign=false", "-c", "init.templateDir=",
                               "-C", str(root), *args], check=True, env=env,
                              capture_output=True, text=True, timeout=5).stdout.strip()

    def enroll(self, root=None, **updates):
        root = root or self.repo
        identity = startup.checkout(root)
        record = {"schema_version": 1, "project_id": "fixture-project",
                  "repository_root": str(identity[0]), "git_common_dir": str(identity[1]),
                  "clients": ["codex"], "policy_digest": startup.policy_digest(PACKAGE)}
        record.update(updates)
        path = root / startup.PROJECT_FILE
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record))
        return path

    def runtime(self, state_dir=None):
        return {"schema_version": 2, "state_dir": str(state_dir or self.base / "state")}

    def canonical(self):
        from tests.fixture_canonical_birth import fresh_canonical, save_test_acquisition
        fixture, acquired, observation = fresh_canonical(self)
        self.base, self.repo = fixture.base, fixture.repo
        self.scope = startup._entry_scope(acquired)
        self.payload['cwd'] = str(self.repo)
        self.test_acquisition = save_test_acquisition(self.base / 'test-acquisition.json', fixture, observation)

    def workflow_command(self):
        script = str(PACKAGE / 'scripts/workflow.py')
        if not hasattr(self, 'test_acquisition'):
            return [sys.executable, '-I', '-B', script]
        # Actual child CLI execution; only its trusted-owner observation is synthetic.
        bootstrap = ('import sys,runpy,json; from pathlib import Path; sys.path.insert(0,' + repr(str(PACKAGE)) + '); '
            'from runtime import startup,changes; '
            'observation=json.loads(Path(' + repr(str(self.test_acquisition)) + ').read_bytes())["observation"]; '
            'original=startup.acquire_project_entry; '
            'startup.acquire_project_entry=lambda *args,**kwargs:original(*args,**(kwargs|dict(qualifier=lambda record,facts:observation))); '
            'changes.LOCK_ROOT=Path(' + repr(str(self.base / 'authority-guards')) + '); '
            'sys.argv=[' + repr(script) + ']+sys.argv[1:]; runpy.run_path(' + repr(script) + ',run_name="__main__")')
        return [sys.executable, '-I', '-B', '-c', bootstrap]

    def invoke(self, event="PreToolUse", payload=None, raw=None, client="codex"):
        payload = self.payload if payload is None else payload
        result = subprocess.run(self.workflow_command() + [client, event], cwd=self.repo,
                                input=raw if raw is not None else json.dumps(payload).encode(),
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout) if result.stdout else None

    def test_legacy_events_are_silent_without_runtime_opt_in(self):
        self.enroll()
        for kind in ("PreToolUse", "PostToolUse", "Stop", "Interrupt"):
            with self.subTest(kind=kind):
                output = self.invoke(kind, {**self.payload, "hook_event_name": kind})
                self.assertEqual(output, {} if kind == "Stop" else None)

    def test_output_notice_requires_enrolled_scope_without_starting_runtime(self):
        payload = {**self.payload, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_response": "Warning: truncated output (original token count: 1489)\nPRIVATE_OUTPUT"}
        self.assertIsNone(self.invoke("PostToolUse", payload))
        self.enroll()
        output = self.invoke("PostToolUse", payload)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("truncation marker", output["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("PRIVATE_OUTPUT", json.dumps(output))
        self.assertFalse((self.repo / ".vibe-workflow" / "runtime.sqlite3").exists())

    def test_runtime_scope_reuses_validated_checkout_and_never_initializes_state(self):
        self.enroll(runtime=self.runtime())
        self.assertTrue(hasattr(startup, "resolve_project"), "reusable scope resolver is missing")
        scope = startup.resolve_project(codex.normalize_runtime(self.payload), PACKAGE)
        self.assertEqual(scope.project_id, "fixture-project")
        self.assertEqual(scope.repository_root, self.repo)
        self.assertEqual(scope.git_common_dir, self.repo / ".git")
        self.assertEqual(scope.runtime.state_dir, self.base / "state")
        self.assertEqual(scope.runtime.schema_version, 2)
        self.assertFalse(scope.runtime.state_dir.exists())

    def test_legacy_resolver_has_no_runtime_settings(self):
        self.enroll()
        self.assertTrue(hasattr(startup, "resolve_project"), "reusable scope resolver is missing")
        event = codex.normalize({"hook_event_name": "SessionStart", "session_id": "session-1",
                                 "cwd": str(self.repo), "source": "startup"})
        scope = startup.resolve_project(event, PACKAGE)
        self.assertIsNone(scope.runtime)
        self.assertEqual(scope.policy_digest, startup.policy_digest(PACKAGE))

    def test_runtime_enabled_patch_is_diagnostic_without_state_writes(self):
        self.enroll(runtime=self.runtime())
        output = self.invoke()
        self.assertIn("original_pinned_package", output["systemMessage"])
        self.assertNotIn("permissionDecision", str(output))
        self.assertNotIn("updatedInput", str(output))
        self.assertFalse((self.base / "state").exists())
        self.assertNotIn("PRIVATE_PATCH", str(output))

    def test_runtime_other_events_report_uncertainty_without_continuing_or_replacing_output(self):
        self.enroll(runtime=self.runtime())
        for kind in ("PreToolUse", "PostToolUse", "Stop", "Interrupt"):
            with self.subTest(kind=kind):
                output = self.invoke(kind, {**self.payload, "hook_event_name": kind,
                                            "tool_name": "Bash", "tool_response": "PRIVATE_RESULT"})
                self.assertNotIn("not implemented", str(output))
                self.assertNotIn("decision", output)
                self.assertNotIn("continue", output)
                self.assertNotIn("PRIVATE_RESULT", str(output))
                self.assertNotIn("SessionStart", str(output))
        self.assertFalse((self.base / "state").exists())

    def task_cli(self, action, request=None, *options):
        result = subprocess.run(self.workflow_command() + ["task", action, "--cwd", str(self.repo), *options],
                                input=json.dumps(request).encode() if request is not None else b"",
                                capture_output=True, timeout=5)
        self.assertEqual(result.stderr, b"")
        return result.returncode, json.loads(result.stdout)

    def test_task_cli_start_update_status_and_validation(self):
        self.canonical()
        request = dict(client="codex", session_id="session-1", task_id="cli-task", scope_ref="user:task",
                       mode="editable", decision_ref="user:request")
        code, output = self.task_cli("start", request)
        self.assertEqual(code, 0, output)
        self.assertEqual(output["task"]["state"], 'TRIAGE')
        self.assertTrue(output["ok"])
        update = {key: request[key] for key in ("client", "session_id", "task_id", "scope_ref")} | dict(
            expected_revision=1, state='WORKING', reason="Implement request", reference="user:request")
        code, output = self.task_cli("update", update)
        self.assertEqual(code, 0, output)
        self.assertEqual(output["task"]["revision"], 2)
        self.assertIsNone(self.invoke())
        code, output = self.task_cli("status", None, "--client", "codex", "--session", "session-1")
        self.assertEqual(code, 0, output)
        self.assertEqual(output["task"]["task_id"], "cli-task")
        for invalid in (update, {**update, "state": 'DONE'}, {**update, "unexpected": True}):
            code, output = self.task_cli("update", invalid)
            self.assertEqual(code, 1, output)
            self.assertFalse(output["ok"])
            self.assertNotIn("Traceback", str(output))

    def test_evidence_cli_trivial_completion_and_separate_merge_evidence(self):
        self.canonical()
        (self.repo / 'note.txt').write_text('fixture')
        self.git(self.repo, 'add', '.')
        self.git(self.repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
        identity = dict(client='codex', session_id='session-1', task_id='evidence-cli', scope_ref='user:task')
        self.assertEqual(self.task_cli('start', identity | dict(mode='editable', decision_ref='user:accepted'))[0], 0)
        def evidence(parts, request=None, *flags):
            result = subprocess.run(self.workflow_command() + [
                'evidence', *parts, '--cwd', str(self.repo), *flags], input=json.dumps(request).encode() if request else b'',
                capture_output=True, timeout=10)
            self.assertEqual(result.stderr, b'')
            return result.returncode, json.loads(result.stdout)
        applicability = dict(applicability='inapplicable', reason='Small text change has no program boundary impact', independent=False)
        plan = dict(version=1, decision_ref='user:accepted', criteria=[dict(id='static', checks=['unit'], behavior=False)],
            checks=[dict(id='unit', argv=[sys.executable, '-c', 'pass'], cwd='.', timeout=3, env={'PATH': os.defpath},
                effects=dict(kind='read-only', inspection_ref='code:inspected', network=False, production=False,
                             publish=False, migrations=False), baseline=dict(status='passed', reference='baseline:1'), dependencies=['note.txt'])],
            reviews={'general': applicability, 'security': applicability},
            security_impact=dict(status='assessed', reason='Text change checked against boundaries', reference='code:impact'),
            base='HEAD', comparison='endpoint', nontrivial=False, instructions=[], exclusions=[], review_limit=1)
        code, result = evidence(['plan'], identity | dict(expected_revision=1, plan=plan))
        self.assertEqual(code, 0, result)
        code, result = evidence(['check'], identity | dict(expected_revision=result['task']['revision'], check_id='unit'))
        self.assertEqual(code, 0, result)
        self.assertEqual(result['attempt']['status'], 'uncertain')
        # This fixture supplies an explicit synthetic host observation, separate from execution result.
        attempt = result['attempt']
        code, observed = evidence(['status'], None, '--client', 'codex', '--session', 'session-1')
        self.assertEqual(code, 0, observed)
        proof = dict(version=1, kind='check-writer-reconciliation', identity=identity, expected_pins=result['task']['pins'],
            expected_revision=result['task']['revision'], scope_id=observed['scope_id'], attempt_id=attempt['id'],
            candidate_digest=attempt['candidate_digest'], current_candidate_digest=observed['evidence']['candidate_digest'],
            plan_digest=attempt['plan_digest'], execution_digest=attempt['execution']['digest'], stopped=True,
            descendants_stopped=True, observation_ref='fixture:explicit-host-observation', coordinator_ref='fixture:checked')
        proof.update(version=2, dispatch_id=attempt['dispatch_id'], assignment_digest=attempt['ledger_assignment_digest'],
            claims_digest=attempt['ledger_claims_digest'])
        raw = json.dumps(proof).encode()
        host_path = self.base / 'check-host-proof.json'
        host_path.write_bytes(raw)
        host_path.chmod(0o400)
        code, result = evidence(['reconcile-check'], identity | dict(expected_revision=result['task']['revision'],
            attempt_id=attempt['id'], host_proof=dict(path=str(host_path), sha256=hashlib.sha256(raw).hexdigest())))
        self.assertEqual(code, 0, result)
        self.assertEqual(result['attempt']['status'], 'passed')
        code, result = evidence(['verify'], identity | dict(expected_revision=result['task']['revision'], purpose='completion'))
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['task']['state'], 'DONE')
        code, status = evidence(['status'], None, '--client', 'codex', '--session', 'session-1')
        self.assertEqual(code, 0, status)
        self.assertEqual(status['task']['state'], 'DONE')
        code, result = evidence(['verify'], identity | dict(expected_revision=status['task']['revision'], purpose='merge-evidence'))
        self.assertEqual(code, 0, result)
        self.assertFalse(result['verified'])
        self.assertFalse(result['merge_authority'])
        code, result = evidence(['review', 'collect'], identity | dict(expected_revision=result['task']['revision'],
                            attempt_id='forged', parent_turn_id='turn', spawn_item_id='spawn'))
        self.assertEqual(code, 1, result)
        self.assertNotIn('Traceback', str(result))

    def test_task_cli_unconfigured_scope_does_not_initialize(self):
        self.enroll()
        code, output = self.task_cli("start", dict(client="codex", session_id="session", task_id="task",
            scope_ref="user:task", mode="editable", decision_ref="user:request"))
        self.assertEqual(code, 1, output)
        self.assertFalse((self.base / "state").exists())

    def test_doctor_cli_reports_missing_enrollment_without_initialization(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
            "doctor", "--cwd", str(self.repo), "--client", "codex", "--executable", "/unavailable-native"],
            capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual(report.get("enrollment", {}).get("status"), "missing")
        self.assertEqual(report.get("package", {}).get("status"), "passed")
        self.assertEqual(report.get("execution", {}).get("status"), "unverified")
        self.assertFalse((self.base / "state").exists())

    def incomplete_package(self):
        installed = self.base / "incomplete-package"
        shutil.copytree(PACKAGE, installed, ignore=shutil.ignore_patterns(".git", ".superpowers", "__pycache__"))
        (installed / "runtime/state.py").unlink()
        self.enroll(runtime=self.runtime())
        return installed

    def test_doctor_cli_missing_state_module_retains_independent_json_report(self):
        installed = self.incomplete_package()
        result = subprocess.run([sys.executable, "-I", "-B", str(installed / "scripts/workflow.py"),
            "doctor", "--cwd", str(self.repo), "--client", "codex", "--executable", "/unavailable-native"],
            capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual(report["package"]["status"], "failed")
        self.assertEqual(report["state"]["status"], "unavailable")
        self.assertEqual(report["native"]["status"], "unavailable")
        self.assertEqual(report["execution"]["status"], "unverified")
        self.assertNotIn("Traceback", result.stdout.decode())
        self.assertFalse((self.base / "state").exists())

    def test_doctor_acquisition_error_keeps_independent_report(self):
        pointer=self.enroll(runtime=self.runtime())
        pointer.write_text('invalid enrollment')
        result=subprocess.run([sys.executable,'-I','-B',str(PACKAGE/'scripts/workflow.py'),
            'doctor','--cwd',str(self.repo),'--client','codex','--executable','/unavailable-native'],
            capture_output=True,timeout=5)
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stderr,b'')
        report=json.loads(result.stdout)
        self.assertEqual(report['package']['status'],'passed')
        self.assertEqual(report['enrollment']['status'],'unavailable')
        self.assertEqual(report['native']['status'],'unavailable')
        self.assertEqual(report['execution']['status'],'unverified')
        self.assertFalse((self.base/'state').exists())

    def test_task_cli_missing_state_module_returns_sanitized_json_failure(self):
        installed = self.incomplete_package()
        result = subprocess.run([sys.executable, "-I", "-B", str(installed / "scripts/workflow.py"),
            "task", "status", "--cwd", str(self.repo), "--client", "codex", "--session", "session-1"],
            capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])
        self.assertIn("error", report)
        self.assertNotIn("runtime.state", report["error"])
        self.assertNotIn("Traceback", result.stdout.decode())
        self.assertFalse((self.base / "state").exists())

    def test_stale_policy_reports_diagnostic_without_vetoing_patch(self):
        self.enroll(policy_digest="stale", runtime=self.runtime())
        output = self.invoke()
        self.assertNotIn("permissionDecision", str(output))
        self.assertFalse((self.base / "state").exists())
        self.enroll(policy_digest="stale")
        output = self.invoke()
        self.assertIn("differ", output.get("systemMessage", ""))
        self.assertNotIn("permissionDecision", str(output))
        self.enroll(policy_digest="stale", runtime=self.runtime(), clients=["claude-code"])
        self.assertIsNone(self.invoke())

    def test_unrelated_and_wrong_client_runtime_enrollment_are_silent(self):
        self.enroll(runtime=self.runtime(), clients=["claude-code"])
        self.assertIsNone(self.invoke())
        neighbor = self.base / "neighbor"
        neighbor.mkdir()
        self.git(neighbor, "init", "-q")
        self.enroll(runtime=self.runtime())
        self.assertIsNone(self.invoke(payload={**self.payload, "cwd": str(neighbor)}))

    def test_runtime_worktree_needs_its_own_enrollment(self):
        self.enroll(runtime=self.runtime())
        self.git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", "fixture")
        linked = self.base / "linked"
        self.git(self.repo, "worktree", "add", "--detach", str(linked))
        payload = {**self.payload, "cwd": str(linked)}
        self.assertIsNone(self.invoke(payload=payload))
        self.enroll(linked, runtime=self.runtime())
        output = self.invoke(payload=payload)
        self.assertNotIn("permissionDecision", str(output))
        self.enroll(linked, repository_root=str(self.repo), runtime=self.runtime())
        output = self.invoke(payload=payload)
        self.assertIn("does not cover", output["systemMessage"])
        self.assertNotIn("permissionDecision", str(output))

    def test_missing_policy_or_client_sources_raise_scoped_runtime_error(self):
        self.enroll(runtime=self.runtime())
        event = codex.normalize_runtime(self.payload)
        with patch("runtime.startup.policy_digest", side_effect=startup.StartupError("Policy source unavailable")):
            with self.assertRaises(startup.RuntimeScopeError):
                startup.resolve_project(event, PACKAGE)
        original = Path.is_file
        with patch.object(Path, "is_file", lambda path: False if path == PACKAGE / "clients/codex.md" else original(path)):
            with self.assertRaises(startup.RuntimeScopeError):
                startup.resolve_project(event, PACKAGE)
        self.assertFalse((self.base / "state").exists())

    def test_malformed_runtime_config_is_error_without_fallback(self):
        for runtime in (None, [], {}, {"schema_version": True, "state_dir": str(self.base / "state")},
                        {"schema_version": 1, "state_dir": str(self.base / "state")},
                        {"schema_version": 2, "state_dir": 12}, self.runtime(Path("relative")),
                        self.runtime(self.repo / "state"), self.runtime(PACKAGE / "state"),
                        self.runtime(self.base / ".codex/plugins/cache/state")):
            with self.subTest(runtime=runtime):
                self.enroll(runtime=runtime)
                output = self.invoke()
                self.assertIn("runtime", output.get("systemMessage", "").lower())
                self.assertNotIn("permissionDecision", str(output))

    def test_state_directory_must_be_canonical_and_outside_any_repository(self):
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        other = self.base / "other-repo"
        other.mkdir()
        self.git(other, "init", "-q")
        for state_dir in (alias / "state", other / "state"):
            with self.subTest(state_dir=state_dir):
                self.enroll(runtime=self.runtime(state_dir))
                self.assertIn("runtime", self.invoke().get("systemMessage", "").lower())

    def test_state_directory_rejects_existing_and_missing_descendants_of_bare_repository(self):
        bare = self.base / "archive"
        bare.mkdir()
        self.git(bare, "init", "--bare", "-q")
        existing = bare / "runtime"
        existing.mkdir()
        event = codex.normalize_runtime(self.payload)
        for state_dir in (existing, bare / "missing/deeper/runtime"):
            with self.subTest(state_dir=state_dir):
                self.enroll(runtime=self.runtime(state_dir))
                with patch.dict(os.environ, {"GIT_DIR": str(self.repo / ".git"),
                                             "GIT_WORK_TREE": str(self.repo),
                                             "GIT_CEILING_DIRECTORIES": str(bare),
                                             "GIT_DISCOVERY_ACROSS_FILESYSTEM": "0"}):
                    with self.assertRaisesRegex(startup.StartupError, "outside repositories"):
                        startup.resolve_project(event, PACKAGE)
                self.assertFalse((state_dir / "runtime.sqlite3").exists())
        self.assertFalse((bare / "missing").exists())

    def test_state_directory_cannot_descend_from_a_regular_file(self):
        parent = self.base / "regular-file"
        parent.write_text("private fixture")
        self.enroll(runtime=self.runtime(parent / "state"))
        output = self.invoke()
        self.assertIn("directory", output.get("systemMessage", "").lower())
        self.assertNotIn("permissionDecision", str(output))

    def test_basic_startup_ignores_oversized_native_identity(self):
        self.canonical()
        original = {str(p): p.read_bytes() for p in self.scope.runtime.state_dir.rglob("*") if p.is_file()}
        payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(self.repo),
                   "session_id": "session-1"}
        normal = self.invoke("SessionStart", payload)
        payload["session_id"] = "word " * 160
        output = self.invoke("SessionStart", payload)
        self.assertEqual(output, normal)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("applies to this enrolled checkout", context)
        self.assertNotIn(payload["session_id"], context)
        self.assertLessEqual(len(context.split()), 150)
        from tests.fixture_canonical_birth import test_acquisition
        with self.assertRaisesRegex(startup.StartupError, "context limit"):
            startup.orient(codex.normalize(payload), PACKAGE, acquisition=test_acquisition(self.scope))
        self.assertEqual(original, {str(p): p.read_bytes() for p in self.scope.runtime.state_dir.rglob("*") if p.is_file()})

    def test_enrolled_runtime_startup_keeps_basic_guidance_and_state_unchanged(self):
        self.canonical()
        original = {str(p): p.read_bytes() for p in self.scope.runtime.state_dir.rglob("*") if p.is_file()}
        payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": str(self.repo),
                   "session_id": "session-1"}
        output = self.invoke("SessionStart", payload)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(PACKAGE / "scripts/read_instructions.py"), context)
        self.assertIn(str(self.repo / startup.PROJECT_FILE), context)
        self.assertNotIn(str(self.scope.runtime.state_dir / "runtime.sqlite3"), context)
        self.assertNotIn("session-1", context)
        self.assertLessEqual(len(context.split()), 150)
        self.assertEqual(original, {str(p): p.read_bytes() for p in self.scope.runtime.state_dir.rglob("*") if p.is_file()})

    def test_runtime_input_is_bounded_and_errors_are_event_specific(self):
        for kind in ("PreToolUse", "PostToolUse", "Stop", "Interrupt"):
            for raw in (b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff",
                        b"PRIVATE_SENTINEL", b"x" * (startup.MAX_INPUT + 1)):
                with self.subTest(kind=kind, raw=raw[:16]):
                    output = self.invoke(kind, raw=raw)
                    self.assertIn(kind, output.get("systemMessage", ""))
                    self.assertNotIn("SessionStart", str(output))
                    self.assertNotIn("PRIVATE_SENTINEL", str(output))
                    self.assertNotIn("decision", output)

    def test_cli_event_must_match_payload_and_claude_remains_startup_only(self):
        self.enroll(runtime=self.runtime())
        output = self.invoke("Stop")
        self.assertIn("Stop", output.get("systemMessage", ""))
        self.assertNotIn("decision", output)
        output = self.invoke(client="claude-code")
        self.assertIn("unsupported", output.get("systemMessage", "").lower())
        self.assertNotIn("permissionDecision", str(output))


class RuntimeAdapterTests(unittest.TestCase):
    def test_output_notice_keeps_normal_quoted_and_malformed_results_silent(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash"}
        existing = {"systemMessage": "Existing feedback"}
        for response in (None, {}, [], "", "Complete output", 'Quoted: Warning: truncated output (original token count: 10)'):
            with self.subTest(response=response):
                self.assertIs(codex.with_output_notice({**payload, "tool_response": response}, existing), existing)
        warning = "Warning: truncated output (original token count: 10)\nPRIVATE_OUTPUT"
        for value in (None, [], {}, {**payload, "hook_event_name": "PreToolUse", "tool_response": warning},
                      {**payload, "tool_name": "apply_patch", "tool_response": warning}):
            with self.subTest(payload=value):
                self.assertIs(codex.with_output_notice(value, existing), existing)

    def test_output_notice_is_constant_advisory_even_for_spoofed_stdout(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_response": "Warning: truncated output (original token count: 10)\nPRIVATE_COMMAND"}
        output = codex.with_output_notice(payload, None)
        specific = output["hookSpecificOutput"]
        self.assertEqual(set(output), {"hookSpecificOutput"})
        self.assertEqual(set(specific), {"hookEventName", "additionalContext"})
        self.assertEqual(specific["hookEventName"], "PostToolUse")
        self.assertIn("truncation marker", specific["additionalContext"])
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertEqual(codex.with_output_notice({**payload, "tool_response": payload["tool_response"].replace("10)", "999)")}, None), output)

    def test_output_notice_preserves_existing_feedback_without_mutation(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_response": "Warning: truncated output (original token count: 10)"}
        existing = {"systemMessage": "Title notification", "hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": "x" * 1024}}
        before = json.dumps(existing, sort_keys=True)
        output = codex.with_output_notice(payload, existing)
        self.assertEqual(json.dumps(existing, sort_keys=True), before)
        self.assertEqual(output["systemMessage"], "Title notification")
        self.assertTrue(output["hookSpecificOutput"]["additionalContext"].startswith("x" * 1024 + "\n"))
        self.assertIn("truncation marker", output["hookSpecificOutput"]["additionalContext"])

    def normalize(self, **updates):
        self.assertTrue(hasattr(codex, "normalize_runtime"), "runtime normalizer is missing")
        payload = {"hook_event_name": "PreToolUse", "session_id": "session-1", "cwd": "/tmp",
                   "tool_name": "apply_patch", **updates}
        return codex.normalize_runtime(payload)

    def result(self, kind, action, reason="safe reason"):
        self.assertTrue(hasattr(codex, "render_runtime"), "runtime renderer is missing")
        from runtime.engine import HookResult
        return codex.render_runtime(HookResult(kind, action, reason))

    def test_normalizer_preserves_only_event_identity_and_needed_observations(self):
        event = self.normalize(turn_id="turn-1", tool_use_id="call-1", transcript_path="PRIVATE_PATH",
                               tool_input={"command": "PRIVATE_INPUT"}, tool_response="PRIVATE_RESULT")
        self.assertEqual((event.kind, event.client, event.session_id), ("PreToolUse", "codex", "session-1"))
        self.assertEqual(event.cwd, Path("/tmp"))
        self.assertEqual((event.turn_id, event.tool_call_id, event.tool_name),
                         ("turn-1", "call-1", "apply_patch"))
        self.assertNotIn("PRIVATE", repr(event))
        self.assertIsNone(self.normalize().turn_id)
        self.assertIsNone(self.normalize().tool_call_id)

    def test_receipt_fingerprint_requires_stable_identity_and_detects_changed_observations(self):
        first = self.normalize(turn_id="turn-1", tool_use_id="call-1", tool_input={"b": 2, "a": 1})
        reordered = self.normalize(turn_id="turn-1", tool_use_id="call-1", tool_input={"a": 1, "b": 2},
                                   transcript_path="ignored")
        changed = self.normalize(turn_id="turn-1", tool_use_id="call-1", tool_input={"a": 3, "b": 2})
        self.assertEqual(first.receipt_fingerprint, reordered.receipt_fingerprint)
        self.assertNotEqual(first.receipt_fingerprint, changed.receipt_fingerprint)
        self.assertEqual(len(first.receipt_fingerprint), 64)
        self.assertIsNone(self.normalize(tool_use_id="call-1").receipt_fingerprint)
        self.assertIsNone(self.normalize(turn_id="turn-1").receipt_fingerprint)
        post = self.normalize(hook_event_name="PostToolUse", turn_id="turn-1", tool_use_id="call-1",
                              tool_response={"exit_code": 0})
        failed = self.normalize(hook_event_name="PostToolUse", turn_id="turn-1", tool_use_id="call-1",
                                tool_response={"exit_code": 1})
        self.assertNotEqual(post.receipt_fingerprint, failed.receipt_fingerprint)

    def test_normalizer_rejects_malformed_required_and_optional_identity(self):
        for field in ("session_id", "cwd", "turn_id", "tool_use_id", "tool_name"):
            for value in (True, [], 1, "", " ", "bad\0id", "bad\nid", "x" * 4097):
                with self.subTest(field=field, value=repr(value)[:20]):
                    with self.assertRaises(startup.StartupError):
                        self.normalize(**{field: value})
        for updates in ({"cwd": "relative"}, {"hook_event_name": "SessionStart"},
                        {"hook_event_name": []}, {"tool_name": None}):
            with self.subTest(updates=updates), self.assertRaises(startup.StartupError):
                self.normalize(**updates)

    def test_stop_active_requires_boolean_and_absence_is_unknown(self):
        event = self.normalize(hook_event_name="Stop", stop_hook_active=True)
        self.assertTrue(event.stop_hook_active)
        self.assertIsNone(self.normalize(hook_event_name="Stop").stop_hook_active)
        for value in (None, 0, 1, "false", []):
            with self.subTest(value=value), self.assertRaises(startup.StartupError):
                self.normalize(hook_event_name="Stop", stop_hook_active=value)

    def test_non_tool_events_ignore_unused_tool_fields(self):
        for kind in ("Stop", "Interrupt"):
            with self.subTest(kind=kind):
                event = self.normalize(hook_event_name=kind, tool_name=[], tool_use_id={},
                                       last_assistant_message={"private": "PRIVATE"})
                self.assertIsNone(event.tool_name)
                self.assertIsNone(event.tool_call_id)
                self.assertNotIn("PRIVATE", repr(event))

    def test_pass_never_grants_permission_and_stop_always_returns_json(self):
        for kind in ("PreToolUse", "PostToolUse", "Stop", "Interrupt"):
            with self.subTest(kind=kind):
                self.assertEqual(self.result(kind, "pass"), {} if kind == "Stop" else None)

    def test_render_uses_only_supported_event_output_schemas(self):
        self.assertEqual(self.result("PreToolUse", "deny"), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "safe reason"}})
        self.assertEqual(self.result("PostToolUse", "context"), {"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": "safe reason"}})
        self.assertEqual(self.result("Stop", "continue"), {"decision": "block", "reason": "safe reason"})
        for kind in ("Stop", "Interrupt"):
            self.assertEqual(self.result(kind, "context"), {"systemMessage": "safe reason"})

    def test_renderer_sanitizes_controls_and_bounds_reason(self):
        output = self.result("PreToolUse", "deny", "safe\nreason\0\x1b" + "x" * 2000)
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertNotIn("\n", reason)
        self.assertNotIn("\0", reason)
        self.assertNotIn("\x1b", reason)
        self.assertLessEqual(len(reason), 1024)

    def test_renderer_rejects_invalid_action_event_combinations(self):
        for kind, action in (("Interrupt", "continue"), ("PostToolUse", "deny"),
                             ("PreToolUse", "continue"), ("Stop", "deny"),
                             ("SessionStart", "context"), ("Stop", "unknown"),
                             ([], "pass"), ("Stop", [])):
            with self.subTest(kind=kind, action=action), self.assertRaises(startup.StartupError):
                self.result(kind, action)
        for reason in (None, [], "", "\0"):
            with self.subTest(reason=reason), self.assertRaises(startup.StartupError):
                self.result("PreToolUse", "deny", reason)


class OnboardingDispatcherTests(unittest.TestCase):
    def test_onboarding_routes_before_enrollment_and_returns_bounded_json_errors(self):
        for args, raw in ((["onboard", "inspect", "--cwd", "/", "--plan-dir", "/uncreated"], b"{}"),
                          (["onboard", "plan", "--plan-dir", "/uncreated"], b"{}"),
                          (["onboard", "inspect"], b"{}"),
                          (["onboard", "plan", "--plan-dir", "/uncreated"], b'{"x":NaN}')):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
                    *args], input=raw, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b"")
                report = json.loads(result.stdout)
                self.assertFalse(report["ok"])
                self.assertNotIn("enrolled", report["error"])

    def test_application_commands_reject_invalid_requests_without_private_errors(self):
        from runtime.onboarding import MAX_REQUEST
        for action in ("apply", "resume", "rollback"):
            for raw in (b"{}", b"null", b'{"private-marker":NaN}', b"x" * (MAX_REQUEST + 1)):
                with self.subTest(action=action, raw=raw[:40]):
                    result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
                        "onboard", action, "--plan-dir", "/private-marker"],
                        input=raw, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr, b"")
                    report = json.loads(result.stdout)
                    if "ok" in report:
                        self.assertFalse(report["ok"])
                    else:
                        self.assertEqual(report["exit_code"], 1)
                        self.assertEqual(report["state"], "blocked")
                    self.assertNotIn("private-marker", str(report))
            result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
                "onboard", action], input=b"{}", capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, b"")
            self.assertFalse(json.loads(result.stdout)["ok"])

    def test_apply_resume_and_rollback_preserve_disposable_target_and_enrollment(self):
        from tests.test_changes import ChangesTests
        fixture = ChangesTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.base / ".local/share").mkdir(parents=True)
        request = fixture.request()
        target = fixture.repo / "AGENTS.md"
        original = target.read_bytes()
        original_mode = target.stat().st_mode & 0o777
        enrollment = fixture.repo / startup.PROJECT_FILE

        def invoke(action):
            result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
                "onboard", action, "--plan-dir", str(fixture.directory)],
                input=json.dumps(request).encode(), capture_output=True, timeout=5,
                env={**os.environ, "HOME": str(fixture.base)})
            self.assertEqual(result.stderr, b"")
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, report["exit_code"], report)
            return report

        request["approval"]["evidence"] = []
        pending = invoke("apply")
        self.assertEqual(pending["exit_code"], 2, pending)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(enrollment.exists())
        request = fixture.request()
        pending = invoke("apply")
        self.assertEqual(pending["exit_code"], 2, pending)
        self.assertEqual(target.read_text(), "product rules\nnew process\n")
        self.assertFalse(enrollment.exists())
        for action in ("resume", "apply"):
            request = fixture.observe(request)
            report = invoke(action)
            self.assertEqual(report["exit_code"], 0, report)
            self.assertEqual(report["state"], "applied")
            self.assertIn("write-rules", report["completed"])
            self.assertEqual(target.read_text(), "product rules\nnew process\n")
            self.assertEqual(json.loads(enrollment.read_text()), request["manifest"]["enrollment"]["record"])
        for _ in range(2):
            request = fixture.observe(request)
            report = invoke("rollback")
            self.assertEqual(report["exit_code"], 0, report)
            self.assertEqual(report["state"], "rolled_back")
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(target.stat().st_mode & 0o777, original_mode)
            self.assertFalse(enrollment.exists())

    def test_schema_two_transition_dispatch_preserves_legacy_history_and_refuses_reactivation(self):
        from tests.test_changes import ChangesTests
        fixture=ChangesTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        record,original,request,database,old_task=fixture.legacy_transition(unresolved=True)
        old_dir=database.parent
        before={str(p.relative_to(old_dir)):p.read_bytes() for p in old_dir.rglob('*') if p.is_file()}
        target=fixture.repo/startup.PROJECT_FILE
        original_rules=(fixture.repo/'AGENTS.md').read_bytes()
        original_rules_mode=(fixture.repo/'AGENTS.md').stat().st_mode & 0o777
        def invoke(family,parts,payload,*,crash=False):
            bootstrap=f"""
import sys,runpy
from pathlib import Path
sys.path.insert(0,{str(PACKAGE)!r})
from runtime import changes
from runtime.startup import StartupError
changes.LOCK_ROOT=Path({str(fixture.base/'transition-guards')!r})
if {crash!r}:
    def interrupted(stage):
        if stage=='rename':
            raise StartupError('Fixture durable transition interruption')
    changes._checkpoint=interrupted
sys.argv=[{str(PACKAGE/'scripts/workflow.py')!r}]+sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
            env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
            env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
            result=subprocess.run([sys.executable,'-I','-B','-c',bootstrap,family,*parts],input=json.dumps(payload).encode(),
                cwd=fixture.base,env=env,capture_output=True,timeout=12)
            self.assertEqual(result.stderr,b'')
            report=json.loads(result.stdout)
            self.assertNotIn('Traceback',str(report))
            self.assertEqual(result.returncode,report.get('exit_code',1 if not report.get('ok',True) else 0),report)
            return result.returncode,report
        def onboard(action,request,**kwargs):
            return invoke('onboard',[action,'--plan-dir',str(fixture.directory)],request,**kwargs)
        request=fixture.observe(request)
        code,result=onboard('apply',request,crash=True)
        self.assertEqual(code,1,result)
        self.assertFalse(target.exists())
        self.assertEqual((fixture.directory/'enrollment.backup').read_bytes(),original)
        self.assertFalse((fixture.base/'fresh-state').exists())
        self.assertEqual((fixture.repo/'AGENTS.md').read_bytes(),original_rules)
        self.assertEqual((fixture.repo/'AGENTS.md').stat().st_mode & 0o777,original_rules_mode)
        self.assertEqual({str(p.relative_to(old_dir)):p.read_bytes() for p in old_dir.rglob('*') if p.is_file()},before)
        request=fixture.observe(request)
        code,result=onboard('resume',request)
        self.assertEqual(code,2,result)
        self.assertEqual(result['blocked'],[{'id':'transaction','reason':
            'Collect adoption qualification/reviews and bind observations to current inputs before final enrollment'}],result)
        self.assertEqual(result['completed'],['write-rules'])
        self.assertEqual((fixture.repo/'AGENTS.md').read_bytes(),b'product rules\nnew process\n')
        self.assertEqual((fixture.repo/'AGENTS.md').stat().st_mode & 0o777,0o644)
        self.assertFalse(target.exists())
        self.assertFalse((fixture.base/'fresh-state').exists())
        self.assertEqual((fixture.directory/'enrollment.backup').read_bytes(),original)
        self.assertEqual({str(p.relative_to(old_dir)):p.read_bytes() for p in old_dir.rglob('*') if p.is_file()},before)
        request=fixture.observe(request)
        code,result=onboard('resume',request)
        self.assertEqual(code,2,result)
        self.assertEqual(result['blocked'],[{'id':'transaction','reason':
            'Enrollment runtime state/pins are incompatible; reconcile them before activation'}],result)
        self.assertEqual(result['completed'],['write-rules'])
        self.assertEqual((fixture.repo/'AGENTS.md').read_bytes(),b'product rules\nnew process\n')
        self.assertEqual((fixture.repo/'AGENTS.md').stat().st_mode & 0o777,0o644)
        self.assertFalse(target.exists())
        self.assertFalse((fixture.base/'fresh-state').exists())
        self.assertEqual((fixture.directory/'enrollment.backup').read_bytes(),original)
        self.assertEqual({str(p.relative_to(old_dir)):p.read_bytes() for p in old_dir.rglob('*') if p.is_file()},before)
        code,repeat=onboard('resume',fixture.observe(request))
        self.assertEqual(code,2,repeat)
        self.assertEqual(repeat['blocked'],result['blocked'],repeat)
        self.assertEqual(repeat['completed'],['write-rules'])
        code,status=invoke('delegation',['status','--cwd',str(fixture.repo),'--client','codex','--session','old-session'],{})
        self.assertEqual(code,1,status)
        self.assertEqual(status['error'],'Enrollment record is missing')
        identity=dict(client='codex',session_id='fresh-session',task_id='fresh-task',scope_ref='user:fresh')
        code,started=invoke('task',['start','--cwd',str(fixture.repo)],identity|dict(mode='editable',decision_ref='user:accepted'))
        self.assertEqual(code,1,started)
        self.assertEqual(started['error'],'Enrollment record is missing')
        self.assertFalse((fixture.base/'fresh-state').exists())
        code,result=onboard('rollback',fixture.observe(request))
        self.assertEqual(code,2,result)
        self.assertTrue(any('incompatible' in item['reason'].lower() for item in result['blocked']),result)
        self.assertFalse(target.exists())
        self.assertEqual((fixture.directory/'enrollment.backup').read_bytes(),original)
        self.assertEqual((fixture.repo/'AGENTS.md').read_bytes(),original_rules)
        self.assertEqual((fixture.repo/'AGENTS.md').stat().st_mode & 0o777,original_rules_mode)
        self.assertEqual({str(p.relative_to(old_dir)):p.read_bytes() for p in old_dir.rglob('*') if p.is_file()},before)
        self.assertEqual(old_task['pins']['schema_version'],1)
        with self.assertRaises(startup.StartupError):
            startup.runtime_settings(record,PACKAGE,Path(record['git_common_dir']))
        # Current task/delegation success is covered by genuinely born canonical dispatcher fixtures.

    def test_inspect_and_plan_work_without_enrollment_and_never_activate_runtime(self):
        from tests.test_onboarding import OnboardingTests
        fixture = OnboardingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        def invoke(action, request):
            args = ["onboard", action, "--plan-dir", str(fixture.directory)]
            if action == "inspect":
                args.extend(["--cwd", str(fixture.repo)])
            result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
                *args], input=json.dumps(request).encode(), capture_output=True, timeout=5)
            self.assertEqual(result.stderr, b"")
            return result.returncode, json.loads(result.stdout)
        code, report = invoke("inspect", fixture.record)
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "draft")
        code, report = invoke("plan", fixture.proposal(1))
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "ready_for_review")
        for field, value in (("dependencies", [{}]), ("id", [])):
            malformed = fixture.proposal(report["revision"])
            malformed["actions" if field == "dependencies" else "decisions"] = ([{
                **malformed["actions"][0], field: value}] if field == "dependencies" else [{"id": value}])
            code, failure = invoke("plan", malformed)
            self.assertEqual(code, 1, failure)
            self.assertFalse(failure["ok"])
        (fixture.directory / "plan.json").write_text('{"schema_version":1,"revision":2}')
        code, failure = invoke("inspect", fixture.record)
        self.assertEqual(code, 1, failure)
        self.assertFalse(failure["ok"])
        self.assertFalse((fixture.repo / startup.PROJECT_FILE).exists())
        self.assertFalse(any(fixture.base.rglob("*.sqlite3")))


if __name__ == '__main__':
    unittest.main()
