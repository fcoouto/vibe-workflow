"""Claude Code setup qualification: observed event stream -> retained receipt -> active qualification-only scope.

Streams are synthetic but shaped exactly like a recorded `--output-format stream-json --include-hook-events`
run (init event with the loaded plugin; hook_response rows with name, exit code, outcome and stdout). They
establish package behavior; the real observation is recorded separately in the validation record.
"""
import copy
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime import changes, setup_observation as setup, startup, state
from runtime.adapters import claude_code, claude_setup
from tests.fixture_canonical_birth import CanonicalBirthFixture

PACKAGE = state.PACKAGE


def stream(package, checkout, authority, *, version="2.1.311", plugin_version=None, plugin_path=None, cwd=None,
           hook_exit=0, context=True, extra=()):
    manifest = json.loads((package / ".claude-plugin/plugin.json").read_text())
    text = ("Vibe Coding Workflow applies to this enrolled checkout. Read " f"{json.dumps(str(package / 'policies/00-index.md'))}"
            f" and {json.dumps(str(authority))}.") if context else "Unrelated context"
    rows = [
        {"type": "system", "subtype": "init", "claude_code_version": version, "cwd": str(cwd or checkout),
         "session_id": "session-1", "plugins": [{"name": manifest["name"], "path": str(plugin_path or package),
                                                  "source": manifest["name"] + "@inline",
                                                  "version": plugin_version or manifest["version"]}]},
        {"type": "system", "subtype": "hook_started", "hook_name": "SessionStart:startup", "hook_event": "SessionStart"},
        {"type": "system", "subtype": "hook_response", "hook_name": "SessionStart:startup", "hook_event": "SessionStart",
         "stdout": json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}),
         "stderr": "", "exit_code": hook_exit, "outcome": "success" if hook_exit == 0 else "error"},
        {"type": "system", "subtype": "hook_response", "hook_name": "Stop", "hook_event": "Stop",
         "stdout": "{}\n", "stderr": "", "exit_code": 0, "outcome": "success"},
        *extra,
    ]
    return "\n".join(json.dumps(row) for row in rows).encode()


class StreamObservationTests(unittest.TestCase):
    def setUp(self):
        self.checkout = Path(tempfile.mkdtemp(prefix="claude-checkout-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.checkout, ignore_errors=True))
        self.authority = self.checkout / ".vibe-workflow/project.json"

    def observe(self, raw):
        return claude_setup.observe_stream(raw, package=PACKAGE, checkout=self.checkout, authority=self.authority)

    def good(self, **kwargs):
        return self.observe(stream(PACKAGE, self.checkout, self.authority, **kwargs))

    def test_a_real_shaped_run_verifies_startup_context_and_records_every_hook(self):
        facts = self.good()
        self.assertEqual(set(facts["cases"]), {"startup-context"})
        self.assertEqual([(hook["event"], hook["exit_code"]) for hook in facts["hooks"]],
                         [("SessionStart", 0), ("Stop", 0)])
        self.assertEqual(facts["init"]["plugin"]["version"],
                         json.loads((PACKAGE / ".claude-plugin/plugin.json").read_text())["version"])
        proof = claude_setup.case_proof(facts, "startup-context")
        self.assertEqual(proof["reference"], "claude-observation:session-1#startup-context")
        self.assertEqual(len(proof["sha256"]), 64)
        # Hook stdout itself is never retained, only its digest.
        self.assertNotIn("Vibe Coding Workflow applies", json.dumps(facts))

    def test_context_that_does_not_name_this_package_and_checkout_verifies_nothing(self):
        self.assertEqual(self.good(context=False)["cases"], {})

    def test_the_run_must_load_this_package_version_and_checkout(self):
        for kwargs in (dict(plugin_path=self.checkout), dict(plugin_version="0.0.0"), dict(cwd=self.checkout.parent)):
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(startup.StartupError):
                self.good(**kwargs)

    def test_a_failed_hook_or_malformed_stream_invalidates_the_observation(self):
        with self.assertRaises(startup.StartupError):
            self.good(hook_exit=1)
        good = stream(PACKAGE, self.checkout, self.authority)
        for raw in (b"", b"not json", good + b"\n[1]", good.replace(b'"init"', b'"other"'),
                    good + b"\n" + good, b"x" * (claude_setup.MAX_STREAM + 1)):
            with self.subTest(raw=raw[:12]), self.assertRaises(startup.StartupError):
                self.observe(raw)

    def test_retained_proofs_must_be_exactly_the_digest_of_the_retained_facts(self):
        facts = self.good()
        observations = {"startup-context": [claude_setup.case_proof(facts, "startup-context")]}
        bootstrap = [claude_setup.case_proof(facts, "init")]
        claude_setup.verify_facts(facts, observations, bootstrap)
        tampered = copy.deepcopy(facts)
        tampered["hooks"][0]["exit_code"] = 3
        tampered_version = copy.deepcopy(facts)
        tampered_version["init"]["claude_version"] = "9.9.9"
        for changed in (tampered, tampered_version):
            with self.assertRaises(startup.StartupError):
                claude_setup.verify_facts(changed, observations, bootstrap)
        for bad in ({"startup-context": []}, {"pre-tool-enforcement": observations["startup-context"]}):
            with self.assertRaises(startup.StartupError):
                claude_setup.verify_facts(facts, bad, bootstrap)

    def test_only_the_adapter_issues_a_witness(self):
        from runtime import doctor
        facts = self.good()
        witness = claude_setup.issue_witness(binding=dict(accepted_scope_digest="a" * 64,
            attempt_ref=dict(reference="fixture:attempt", sha256="b" * 64), transaction_id="t"),
            executable=sys.executable, package=PACKAGE, facts=facts)
        content = doctor._native_witness_content(witness)
        self.assertEqual(content["native"]["surface"], "claude-code-cli")
        self.assertEqual(set(content["observations"]), {"startup-context"})
        with self.assertRaises(doctor.ObservationError):
            doctor.NativeSetupWitness({}, _issuer=object())


class BundleScopeTests(unittest.TestCase):
    def test_only_the_content_claude_loads_changes_the_configuration_digest(self):
        import shutil
        base = Path(tempfile.mkdtemp(prefix="claude-bundle-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        package, checkout, config_dir = base / "package", base / "checkout", base / "config"
        for directory in (".claude-plugin", "hooks", "clients", "docs", "tests", "assets"):
            (package / directory).mkdir(parents=True)
        for relative in (".claude-plugin/plugin.json", "hooks/claude-code.json", "clients/a.md", "docs/a.md",
                         "tests/t.py", "assets/a.bin"):
            (package / relative).write_text("{}" if relative.endswith("json") else "x")
        checkout.mkdir()
        config_dir.mkdir()
        config = dict(package_root=str(package), claude_config=str(config_dir), plugin_name="p",
                      roots=[str(checkout), str(checkout)])
        digest = lambda: setup.current_configuration(config, checkout)["digest"]
        original = digest()
        for ignored in ("docs/a.md", "tests/t.py", "assets/a.bin"):
            (package / ignored).write_text("changed " + ignored)
            self.assertEqual(digest(), original, ignored)
        for loaded in ("clients/a.md", "hooks/claude-code.json"):
            (package / loaded).write_text("changed " + loaded)
            self.assertNotEqual(digest(), original, loaded)
            original = digest()


class ClaudeQualificationTests(unittest.TestCase):
    def setUp(self):
        self.config = Path(tempfile.mkdtemp(prefix="claude-config-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.config, ignore_errors=True))
        for target in (patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config)}),
                       patch.object(claude_code, "MANAGED_DIRECTORIES", {})):
            target.start()
            self.addCleanup(target.stop)
        birth = CanonicalBirthFixture("runTest")
        birth.client = "claude-code"
        self.addCleanup(birth.doCleanups)
        self.fixture, self.acquired, _ = birth.accepted_birth()
        self.identity = startup._canonical_plain(self.acquired.resolution.scope.accepted_identity)
        self.journal = copy.deepcopy(changes._load_journal(self.fixture.directory))
        self.proof = dict(reference="fixture:independent-assessment", sha256="a" * 64)

    def witness(self, journal=None, **kwargs):
        journal = journal or self.journal
        authority = self.acquired.authority_path
        facts = claude_setup.observe_stream(stream(PACKAGE, self.fixture.repo, authority, **kwargs),
                                            package=PACKAGE, checkout=self.fixture.repo, authority=authority)
        binding = dict(accepted_scope_digest=startup._canonical_digest(self.identity),
                       attempt_ref=dict(reference="fixture:approved-claude-observation", sha256="c" * 64),
                       transaction_id=journal["transaction_id"])
        self.attempt_ref = binding["attempt_ref"]
        return claude_setup.issue_witness(binding=binding, executable=sys.executable, package=PACKAGE, facts=facts), facts

    def assessment(self, witness_facts):
        proof = claude_setup.case_proof(witness_facts, "startup-context")
        required = self.journal["manifest"]["enrollment"]["qualification"][0]["requirements"]
        cases = []
        for case in setup.BEHAVIOR_CASES:
            if case == "startup-context":
                cases.append(dict(id=case, status="verified", evidence=[proof]))
            elif case == "interrupt-ownership":
                cases.append(dict(id=case, status="inapplicable", evidence=[self.proof],
                                  reason="Claude Code has no interrupt hook; Stop does not run on a user interrupt"))
            else:
                cases.append(dict(id=case, status="pending", evidence=[]))
        return dict(cases=cases, requirements=[dict(id=name, status="pending", evidence=[]) for name in required],
                    general_review=[self.proof], security_review=[self.proof],
                    semantic_assessment=dict(complete=True, catalog_complete=True, conflicts_checked=True,
                                             instructions_checked=True, source_provenance_checked=True))

    def collect(self, **kwargs):
        witness, facts = self.witness(**kwargs)
        return setup.collect_setup(witness, self.identity, self.journal["transaction_id"], self.assessment(facts))

    def activate(self):
        """Persist the receipt through D4's owner path and publish the active partial authority."""
        issued = self.collect()
        journal, fixture = self.journal, self.fixture
        record = journal["manifest"]["enrollment"]["record"]
        key = next(iter(changes._scoped_entries(record)))
        entry = changes._scoped_entries(record)[key][2]
        binding = setup.receipt_binding(issued, fixture.directory)
        entry["activation"].update(status="active", qualification="qualified", transaction_id=journal["transaction_id"],
            accepted_scope_digest=binding["accepted_scope_digest"], qualification_binding=binding, evidence=[binding["receipt"]])
        journal["manifest_digest"] = changes.d3._digest(journal["manifest"])
        task = dict(client="claude-code", session_id="setup", task_id="guard-probe")
        authorization = dict(decision_reference=self.proof, target=dict(
            accepted_scope_digest=binding["accepted_scope_digest"], repository_root=str(fixture.repo),
            git_common_dir=self.identity["git_common_dir"], disposable=True), attempts=[dict(
                reference=self.attempt_ref, task=task, native_sessions=["setup"], operations=["task-start"],
                capability_keys=["pre-tool-enforcement"], limits=dict(deadline=time.time() + 120, max_attempts=1,
                                                                        max_resource_units=1),
                stop_conditions=["Stop after one observed guard outcome"])])
        journal["setup_receipts"] = {}
        setup.persist_setup(fixture.directory, journal, [issued], qualification_scope=authorization)
        journal["state"] = "applied"
        journal["blocked"] = []
        raw = json.dumps(record, sort_keys=True, ensure_ascii=True).encode() + b"\n"
        self.acquired.authority_path.write_bytes(raw)
        target = changes._scoped_target(Path(record["project_root"]), str(changes.PROJECT_FILE))
        for post in journal["enrollment"]["postimages"]:
            if post["phase"] == "final":
                post.update(raw_sha256=hashlib.sha256(raw).hexdigest(), target=target,
                            after=dict(sha256=hashlib.sha256(raw).hexdigest(), mode=0o600))
        for member in journal["enrollment"]["members"]:
            member.update(status="active", blocked=None)
        changes._save(fixture.directory, journal)
        return issued

    def acquire(self):
        return startup.acquire_project_entry(SimpleNamespace(client="claude-code", cwd=self.fixture.repo), PACKAGE)

    def test_a_real_receipt_activates_a_qualification_only_scope_with_only_startup_verified(self):
        issued = self.activate()
        content = startup._canonical_plain(issued.content)
        self.assertEqual((content["native"]["surface"], content["version"], content["predecessor"]),
                         ("claude-code-cli", 2, None))
        self.assertEqual(set(content["source"]["observations"]), {"startup-context"})
        self.assertNotIn("codex_home", content["configuration"])
        acquired = self.acquire()
        self.assertEqual(acquired.resolution.status, "active", acquired.resolution)
        scope = acquired.resolution.scope
        self.assertEqual(tuple(scope.qualification["verified"]), ("startup-context",))
        self.assertFalse(scope.qualification["complete"])
        self.assertTrue(startup.qualification_only(scope))
        self.assertEqual(scope.client, "claude-code")

    def test_changed_observable_configuration_leaves_activation_unqualified(self):
        self.activate()
        self.assertEqual(self.acquire().resolution.status, "active")
        (self.config / "settings.json").write_text(json.dumps({"disableAllHooks": True}))
        self.assertNotEqual(self.acquire().resolution.status, "active")
        (self.config / "settings.json").unlink()
        self.assertEqual(self.acquire().resolution.status, "active")
        (self.fixture.repo / ".claude").mkdir(exist_ok=True)
        (self.fixture.repo / ".claude/settings.local.json").write_text(json.dumps({"hooks": {"Stop": []}}))
        self.assertNotEqual(self.acquire().resolution.status, "active")

    def test_unrelated_settings_never_change_the_configuration_digest(self):
        self.activate()
        (self.config / "settings.json").write_text(json.dumps({"model": "other", "env": {"SECRET": "x"}}))
        self.assertEqual(self.acquire().resolution.status, "active")

    def test_disabled_hooks_plugin_or_managed_policy_cannot_be_qualified(self):
        (self.config / "settings.json").write_text(json.dumps({"disableAllHooks": True}))
        with self.assertRaisesRegex(startup.StartupError, "disabled"):
            self.collect()
        name = json.loads((PACKAGE / ".claude-plugin/plugin.json").read_text())["name"]
        (self.config / "settings.json").write_text(json.dumps({"enabledPlugins": {name + "@x": False}}))
        with self.assertRaisesRegex(startup.StartupError, "disabled"):
            self.collect()
        (self.config / "settings.json").write_text("{}")
        managed = self.config / "managed"
        managed.mkdir()
        (managed / "managed-settings.json").write_text(json.dumps({"allowManagedHooksOnly": True}))
        with patch.object(claude_code, "MANAGED_DIRECTORIES", {sys.platform: str(managed)}):
            with self.assertRaisesRegex(startup.StartupError, "Managed"):
                self.collect()

    def test_receipts_are_refused_when_their_facts_or_surface_do_not_hold(self):
        content = startup._canonical_plain(self.collect().content)
        setup.receipt_shape(content)
        for mutate in (lambda value: value["source"]["claude_observation"]["hooks"][0].update(exit_code=9),
                       lambda value: value["source"]["claude_observation"]["init"].update(claude_version="1.0.0"),
                       lambda value: value["source"].pop("claude_observation"),
                       lambda value: value["native"].update(surface="codex-cli"),
                       lambda value: value["source"].update(native_origin=dict(reference="x#native-observation:y", sha256="d" * 64))):
            changed = copy.deepcopy(content)
            mutate(changed)
            with self.subTest(), self.assertRaises(startup.StartupError):
                setup.receipt_shape(changed)

    def test_claude_successors_wait_for_a_managed_behavior_source(self):
        issued = self.collect()
        reference = dict(reference=str(self.fixture.directory / "transaction.json") + "#setup:" + "a" * 64, sha256="b" * 64)
        with self.assertRaisesRegex(startup.StartupError, "not available yet"):
            setup.collect_retained_setup(reference, self.identity, "next", self.assessment(
                startup._canonical_plain(issued.content)["source"]["claude_observation"]), witness=object())
        with self.assertRaisesRegex(startup.StartupError, "managed behavior source"):
            setup.collect_setup(self.witness()[0], self.identity, self.journal["transaction_id"],
                                self.assessment(self.witness()[1]), predecessor=reference)


if __name__ == "__main__":
    unittest.main()
