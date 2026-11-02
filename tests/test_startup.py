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
from runtime.engine import HookResult
from runtime.startup import MAX_INPUT, PROJECT_FILE, StartupError, checkout, orient, policy_digest
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


class CanonicalScopeTests(unittest.TestCase):
    """Synthetic observer facts prove comparison, never native qualification."""

    @staticmethod
    def digest(value):
        import hashlib
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=True).encode()).hexdigest()

    def fixture(self, *, umbrella=False, linked=False):
        b = {"device": 1, "inode": 10}
        ref = {"reference": "fixture-proof", "sha256": "a" * 64}
        root = "/project/repo" if umbrella else "/repo"
        project = "/project" if umbrella else root
        selected = "/linked" if linked else root
        common = root + "/.git"
        private = common + "/worktrees/linked" if linked else common
        bindings = {k: dict(b) for k in ("root", "primary_root", "git_dir", "git_common_dir")}
        config = dict(policy_digest="b" * 64, runtime_digest="c" * 64,
                      runtime=dict(schema_version=3, state_dir="/state/tasks"),
                      shared_dependencies=[project + "/guide.md"], evidence=[dict(ref)])
        identity = dict(version=1, project_id="project", repository_id="repository", checkout_id="checkout",
                        project_root=project, project_binding=b, primary_root=root, repository_root=selected,
                        git_dir=private, git_common_dir=common, bindings=bindings, client="codex",
                        membership=dict(included=True, authorization=ref),
                        configuration={k: config[k] for k in ("policy_digest", "runtime_digest", "runtime")},
                        shared_policy=[dict(path=project + "/guide.md", sha256="d" * 64)])
        digest = self.digest(identity)
        hook = dict(root="/harness", binding=dict(b), source="/harness/hook", sha256="e" * 64)
        q = dict(client="codex", surface="codex-desktop", version="1", configuration_digest="f" * 64,
                 hook_owner=hook, accepted_scope_digest=digest, receipt=dict(ref))
        activation = dict(status="active", qualification="qualified", accepted_scope_digest=digest,
                          transaction_id="transaction", evidence=[dict(ref)], qualification_binding=q)
        client = dict(client="codex", configuration=config, activation=activation)
        repo = dict(repository_id="repository", primary_root=root, git_common_dir=common,
                    binding={k: dict(b) for k in ("primary_root", "git_common_dir")},
                    membership="joined", checkouts=[dict(checkout_id="checkout", root=selected,
                        git_dir=private, binding={k: dict(b) for k in ("root", "git_dir")}, clients=[client])])
        record = dict(schema_version=2, revision=1, project_id="project", project_root=project,
                      kind="umbrella" if umbrella else "standalone", project_binding=dict(b),
                      membership=dict(include=["."], exclude=[], future_join=True, authorization=dict(ref)),
                      shared_policy=copy.deepcopy(identity["shared_policy"]), repositories=[repo])
        def target(path, sha):
            return dict(path=path, binding=dict(b), parents={str(Path(path).parent): dict(b)},
                        fingerprint=dict(status="read", sha256=sha, bytes=10))
        authority = dict(path=project + "/" + str(PROJECT_FILE),
                         target=target(project + "/" + str(PROJECT_FILE), "1" * 64))
        authority["target"]["parents"][project] = dict(b)
        observation = dict(project_root=project, project_binding=dict(b), root=selected, primary_root=root,
                           git_dir=private, git_common_dir=common, binding=copy.deepcopy(bindings),
                           membership="included", conflicting_authorities=[],
                           dependencies=[dict(path=project + "/guide.md", status="read",
                                              target=target(project + "/guide.md", "d" * 64))],
                           qualification=dict(current={k: copy.deepcopy(q[k]) for k in
                               ("client", "surface", "version", "configuration_digest", "hook_owner")},
                               receipt=dict(ref), binding=copy.deepcopy(q),
                               provenance=dict(kind="trusted-coordinator", **ref)),
                           runtime_location=dict(path="/state/tasks", existing_ancestors=[
                               dict(path="/state/tasks", binding=None, kind="absent"),
                               dict(path="/state", binding=dict(b), kind="directory"),
                               dict(path="/", binding=dict(b), kind="directory")],
                               git_probe=dict(cwd="/state", result="outside"),
                               exclusions=dict(package_root="/package", software_roots=[project, root, selected],
                                               git_roots=[common, private], plugin_cache_roots=[]),
                               provenance=dict(kind="runtime-location-observer", **ref)))
        installed = dict(policy_digest="b" * 64, runtime_digest="c" * 64,
                         clients=["codex", "claude-code"], package_root="/package")
        return record, dict(authority=authority, observation=observation, client="codex", installed=installed), identity

    def resolve(self, record, args):
        from runtime.startup import decode_project_record, resolve_canonical_scope
        return resolve_canonical_scope(decode_project_record(record), **args)

    def test_canonical_preview_absent_preimage_has_no_current_scope_and_unknown_facts_fail(self):
        from runtime.startup import preview_canonical_scope, decode_project_record
        record, args, _ = self.fixture()
        args["authority"]["target"]["binding"] = None
        args["authority"]["target"]["fingerprint"] = dict(status="absent")
        preview = preview_canonical_scope(decode_project_record(record), **args)
        self.assertTrue(preview.eligible)
        self.assertEqual(preview.reason,"eligible")
        self.assertFalse(hasattr(preview,"scope"))
        self.assertFalse(hasattr(preview,"status"))
        self.assertEqual(self.resolve(record,args).status,"conflict")
        args["observation"]["qualification"] = None
        self.assertFalse(preview_canonical_scope(decode_project_record(record), **args).eligible)

    def test_canonical_projection_configured_historical_and_pending(self):
        from runtime.startup import canonical_scope_projection, decode_project_record
        record, _, identity = self.fixture()
        project = lambda value: canonical_scope_projection(decode_project_record(value),
            repository_id="repository", checkout_id="checkout", client="codex")
        from runtime.startup import _canonical_plain
        self.assertEqual(_canonical_plain(project(record)), identity)
        entry = record["repositories"][0]["checkouts"][0]["clients"][0]
        entry["configuration"]["runtime"] = None
        entry["activation"].update(status="configured", qualification="pending", accepted_scope_digest=None,
            transaction_id=None, evidence=[], qualification_binding=None)
        self.assertIsNone(project(record)["configuration"]["runtime"])
        entry["configuration"] = None
        entry["activation"]["status"] = "pending"
        self.assertIsNone(project(record))

    def test_canonical_projection_removed_excluded_and_invalid_ids(self):
        from runtime.startup import canonical_scope_projection, decode_project_record, StartupError
        record, _, _ = self.fixture()
        for field in ("removed", "excluded"):
            value = copy.deepcopy(record)
            if field == "removed": value["repositories"][0]["membership"] = "removed"
            else: value["membership"]["exclude"] = ["."]
            projection = canonical_scope_projection(decode_project_record(value),
                repository_id="repository", checkout_id="checkout", client="codex")
            self.assertFalse(projection["membership"]["included"])
        for key in ("repository_id", "checkout_id", "client"):
            selection = dict(repository_id="repository", checkout_id="checkout", client="codex")
            selection[key] = "unknown"
            with self.assertRaises(StartupError): canonical_scope_projection(decode_project_record(record), **selection)

    def test_canonical_active_identity_is_selected_and_no_io(self):
        from contextlib import ExitStack
        for umbrella, linked in ((False, False), (True, False), (True, True), (False, True)):
            with self.subTest(umbrella=umbrella, linked=linked):
                record, args, identity = self.fixture(umbrella=umbrella, linked=linked)
                with ExitStack() as stack:
                    for name in ("builtins.open", "os.open", "subprocess.run", "pathlib.Path.resolve",
                                 "pathlib.Path.stat", "pathlib.Path.exists", "pathlib.Path.open",
                                 "os.stat", "os.lstat", "os.scandir", "os.readlink"):
                        stack.enter_context(patch(name, side_effect=AssertionError("pure API performed IO")))
                    result = self.resolve(json.dumps(record).encode(), args)
                self.assertEqual((result.status, result.reason), ("active", "active"))
                self.assertEqual(dict(result.scope.accepted_identity) | {"shared_policy": [dict(v) for v in result.scope.accepted_identity["shared_policy"]]}, identity)
                self.assertEqual(str(result.scope.repository_root), identity["repository_root"])
                self.assertEqual(result.scope.accepted_scope_digest, self.digest(identity))

    def test_canonical_decoder_rejects_malformed_and_bounded_json(self):
        from runtime.startup import decode_project_record, StartupError
        record, _, _ = self.fixture()
        invalid = [b'{"schema_version":2,"schema_version":2}', b'{"x":NaN}', b'[]', b'\xff',
                   b'x' * (MAX_INPUT + 1)]
        for change in (dict(schema_version=True), dict(revision=0), dict(unexpected=1),
                       dict(project_root="/repo/../repo"), dict(kind="bad")):
            invalid.append(record | change)
        bad = copy.deepcopy(record); bad["repositories"] *= 2; invalid.append(bad)
        bad = copy.deepcopy(record); bad["membership"]["include"] = ["../escape"]; invalid.append(bad)
        bad = copy.deepcopy(record); bad["repositories"][0]["binding"]["primary_root"]["inode"] = True; invalid.append(bad)
        for value in invalid:
            with self.subTest(value=str(value)[:70]), self.assertRaises(StartupError):
                decode_project_record(value)

    def test_canonical_dependency_observations_are_current_and_selected(self):
        for change, reason in (("changed", "dependency_changed"), ("missing", "dependency_unobserved"),
                               ("unknown", "dependency_unobserved"), ("absent", "dependency_unobserved")):
            record, args, _ = self.fixture()
            dep = args["observation"]["dependencies"][0]
            if change == "changed": dep["target"]["fingerprint"]["sha256"] = "0" * 64
            if change == "missing": args["observation"]["dependencies"] = []
            if change == "unknown": dep.update(status="unknown", target=None)
            if change == "absent": dep.update(status="absent", target=dict(dep["target"], binding=None, fingerprint=dict(status="absent")))
            with self.subTest(change=change):
                result = self.resolve(record, args)
                self.assertEqual((result.status, result.reason), ("suspended", reason))
                self.assertIsNone(result.scope)

    def test_canonical_missing_or_changed_qualification_never_activates(self):
        for field in (None, "configuration_digest", "hook_owner", "receipt"):
            record, args, _ = self.fixture()
            q = args["observation"]["qualification"]
            if field is None: args["observation"]["qualification"] = None
            elif field == "hook_owner": q["current"][field]["sha256"] = "0" * 64
            elif field == "receipt": q[field]["sha256"] = "0" * 64
            else: q["current"][field] = "changed" if field == "version" else "0" * 64
            with self.subTest(field=field):
                result = self.resolve(record, args)
                self.assertEqual(result.status, "configured" if field is None else "suspended")
                self.assertIsNone(result.scope)

    def test_canonical_version_change_preserves_current_qualification(self):
        record, args, _ = self.fixture()
        args["observation"]["qualification"]["current"]["version"] = "changed"
        self.assertEqual(self.resolve(record, args).status, "active")

    def test_canonical_runtime_location_unknown_or_unsafe_never_activates(self):
        for change, status in (("missing", "configured"), ("unknown", "configured"),
                               ("bare", "conflict"), ("repository", "conflict"),
                               ("link", "conflict"), ("package", "conflict"),
                               ("software", "conflict"), ("git", "conflict"), ("cache", "conflict")):
            record, args, _ = self.fixture()
            loc = args["observation"]["runtime_location"]
            if change == "missing": args["observation"]["runtime_location"] = None
            elif change in ("unknown", "bare", "repository"): loc["git_probe"]["result"] = change
            elif change == "link": loc["existing_ancestors"][1]["kind"] = "link"
            elif change == "package": loc["exclusions"]["package_root"] = "/state"
            else: loc["exclusions"][{"software": "software_roots", "git": "git_roots", "cache": "plugin_cache_roots"}[change]].append("/state")
            with self.subTest(change=change):
                result = self.resolve(record, args)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.scope)

    def test_canonical_identity_conflict_precedes_pending_and_changed_proof(self):
        for key in ("project_binding", "binding", "primary_root", "root", "git_common_dir", "conflicting_authorities"):
            record, args, _ = self.fixture()
            obs = args["observation"]
            if key == "project_binding": obs[key]["inode"] += 1
            elif key == "binding": obs[key]["root"]["inode"] += 1
            elif key == "conflicting_authorities": obs[key] = ["/other/authority"]
            else: obs[key] = "/changed"
            obs["qualification"] = None
            with self.subTest(key=key): self.assertEqual(self.resolve(record, args).status, "conflict")

    def test_canonical_states_exclusions_removal_and_pending_client(self):
        for change, want in (("excluded", "outside"), ("removed", "outside"), ("client", "pending"),
                             ("pending", "pending"), ("configured", "configured"), ("suspended", "suspended")):
            record, args, _ = self.fixture()
            entry = record["repositories"][0]["checkouts"][0]["clients"][0]
            if change == "excluded": record["membership"]["exclude"] = ["."]
            elif change == "removed": record["repositories"][0]["membership"] = "removed"
            elif change == "client": args["client"] = "claude-code"
            else: entry["activation"]["status"] = change
            with self.subTest(change=change): self.assertEqual(self.resolve(record, args).status, want)

    def test_canonical_snapshots_reject_caller_mutation_and_ignore_unrelated_join(self):
        from runtime.startup import decode_project_record, resolve_canonical_scope
        record, args, _ = self.fixture(umbrella=True)
        decoded = decode_project_record(record)
        first = resolve_canonical_scope(decoded, **args)
        record["repositories"][0]["checkouts"][0]["root"] = "/attacker"
        args["observation"]["qualification"]["provenance"]["reference"] = "changed"
        self.assertEqual(str(first.scope.repository_root), "/project/repo")
        self.assertEqual(first.scope.qualification_provenance["reference"], "fixture-proof")
        with self.assertRaises(TypeError): first.scope.accepted_identity["project_id"] = "attacker"
        record, args, _ = self.fixture(umbrella=True)
        second_repo = copy.deepcopy(record["repositories"][0])
        second_repo.update(repository_id="other-repo", primary_root="/project/other", git_common_dir="/project/other/.git")
        second_repo["checkouts"][0].update(checkout_id="other-checkout", root="/project/other", git_dir="/project/other/.git")
        second_repo["checkouts"][0]["clients"] = []
        record["repositories"].append(second_repo)
        record["shared_policy"].append(dict(path="/project/other-guide", sha256="0" * 64))
        record["revision"] += 1
        second = self.resolve(record, args)
        self.assertEqual(second.status, "active")
        self.assertEqual(first.scope.accepted_scope_digest, second.scope.accepted_scope_digest)

    def test_canonical_incomplete_provenance_and_runtime_chain_stay_non_active(self):
        for change in ("qualification-provenance", "qualification-binding", "runtime-provenance", "chain"):
            record, args, _ = self.fixture()
            obs = args["observation"]
            if change == "qualification-provenance": obs["qualification"]["provenance"] = None
            elif change == "qualification-binding": obs["qualification"]["binding"] = None
            elif change == "runtime-provenance": obs["runtime_location"]["provenance"] = None
            else: obs["runtime_location"]["existing_ancestors"].pop()
            with self.subTest(change=change):
                self.assertEqual(self.resolve(record, args).status, "configured")

    def test_canonical_runtime_mismatch_precedes_exclusion_and_dependency_gap(self):
        record, args, _ = self.fixture()
        record["membership"]["exclude"] = ["."]
        record["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["runtime"]["state_dir"] = "/other-state"
        args["observation"]["dependencies"] = []
        self.assertEqual(self.resolve(record, args).status, "conflict")

    def test_canonical_target_and_nested_invalid_types_are_sanitized(self):
        from runtime.startup import StartupError, decode_project_record
        record, args, _ = self.fixture()
        bad = copy.deepcopy(record)
        bad["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["shared_dependencies"] = [[]]
        with self.assertRaises(StartupError): decode_project_record(bad)
        args["authority"]["target"]["parents"] = {"/elsewhere": {"device": 1, "inode": 2}}
        with self.assertRaises(StartupError): self.resolve(record, args)

    def test_canonical_future_primary_is_pending_excluded_or_outside_without_inheritance(self):
        for primary, exclude, expected in (("/project/new", [], "pending"),
                                            ("/project/new", ["new"], "outside"),
                                            ("/elsewhere/new", [], "outside")):
            record, args, _ = self.fixture(umbrella=True)
            record["membership"]["exclude"] = exclude
            obs = args["observation"]
            obs.update(root=primary, primary_root=primary, git_dir=primary + "/.git", git_common_dir=primary + "/.git")
            obs["binding"] = {k: dict(device=1, inode=99) for k in obs["binding"]}
            with self.subTest(primary=primary, exclude=exclude):
                self.assertEqual(self.resolve(record, args).status, expected)
        obs["primary_root"] = "/project/copied"
        obs["binding"]["primary_root"] = dict(device=1, inode=10)
        self.assertEqual(self.resolve(record, args).status, "conflict")

    def test_canonical_unsafe_location_precedes_future_member_pending(self):
        record, args, _ = self.fixture(umbrella=True)
        obs = args["observation"]
        obs.update(root="/project/new", primary_root="/project/new", git_dir="/project/new/.git", git_common_dir="/project/new/.git")
        obs["binding"] = {k: dict(device=1, inode=99) for k in obs["binding"]}
        obs["runtime_location"]["git_probe"]["result"] = "bare"
        self.assertEqual(self.resolve(record, args).status, "conflict")


    def test_canonical_authority_parent_identity_cannot_be_replaced(self):
        record, args, _ = self.fixture()
        args["authority"]["target"]["parents"]["/repo"] = dict(device=1, inode=88)
        self.assertEqual(self.resolve(record, args).status, "conflict")


    def test_canonical_absent_checkout_is_pending_without_reusing_moved_identity(self):
        record, args, _ = self.fixture()
        args["observation"]["root"] = "/new-worktree"
        args["observation"]["binding"]["root"]["inode"] = 99
        self.assertEqual(self.resolve(record, args).status, "conflict")
        args["observation"]["git_dir"] = "/repo/.git/worktrees/new-worktree"
        args["observation"]["binding"]["git_dir"]["inode"] = 98
        self.assertEqual(self.resolve(record, args).status, "pending")


    def test_canonical_different_clients_have_distinct_task_projection_same_checkout(self):
        record, args, identity = self.fixture()
        first = self.resolve(record, args)
        other = copy.deepcopy(record["repositories"][0]["checkouts"][0]["clients"][0])
        identity["client"] = "claude-code"
        other["client"] = "claude-code"
        digest = self.digest(identity)
        other["activation"]["accepted_scope_digest"] = digest
        binding = other["activation"]["qualification_binding"]
        binding.update(client="claude-code", accepted_scope_digest=digest)
        record["repositories"][0]["checkouts"][0]["clients"].append(other)
        args["client"] = "claude-code"
        qualification = args["observation"]["qualification"]
        qualification["binding"] = copy.deepcopy(binding)
        qualification["current"]["client"] = "claude-code"
        result = self.resolve(record, args)
        self.assertEqual(result.status, "active")
        self.assertEqual(first.scope.checkout_id, result.scope.checkout_id)
        self.assertNotEqual(first.scope.accepted_scope_digest, result.scope.accepted_scope_digest)
        self.assertEqual(result.scope.accepted_identity["client"], "claude-code")

    def test_canonical_missing_linked_checkout_cannot_bypass_exclusion_or_common_identity(self):
        for change, want in (("exclude", "outside"), ("common", "conflict")):
            record, args, _ = self.fixture()
            obs = args["observation"]
            obs["root"] = "/new-linked"
            obs["binding"]["root"]["inode"] = 99
            obs["git_dir"] = "/repo/.git/worktrees/new-linked"
            obs["binding"]["git_dir"]["inode"] = 98
            if change == "exclude": record["membership"]["exclude"] = ["."]
            else: obs["binding"]["git_common_dir"]["inode"] = 99
            with self.subTest(change=change): self.assertEqual(self.resolve(record, args).status, want)

    def test_canonical_one_directory_cannot_have_inconsistent_record_bindings(self):
        from runtime.startup import StartupError, decode_project_record
        record, _, _ = self.fixture()
        record["repositories"][0]["checkouts"][0]["binding"]["root"]["inode"] = 77
        with self.assertRaises(StartupError): decode_project_record(record)

    def test_canonical_immutable_observer_inputs_are_supported(self):
        from types import MappingProxyType
        def immutable(value):
            if isinstance(value, dict): return MappingProxyType({k: immutable(v) for k, v in value.items()})
            if isinstance(value, list): return tuple(immutable(v) for v in value)
            return value
        record, args, _ = self.fixture()
        self.assertEqual(self.resolve(immutable(record), immutable(args)).status, "active")


    def test_canonical_read_authority_requires_its_immediate_parent(self):
        from runtime.startup import StartupError
        record, args, _ = self.fixture()
        del args["authority"]["target"]["parents"]["/repo/.vibe-coding-workflow"]
        with self.assertRaises(StartupError): self.resolve(record, args)

    def test_canonical_dependency_parent_must_match_known_directory_identity(self):
        record, args, _ = self.fixture()
        args["observation"]["dependencies"][0]["target"]["parents"]["/repo"]["inode"] = 999
        result = self.resolve(record, args)
        self.assertEqual((result.status, result.reason), ("suspended", "dependency_changed"))

    def test_canonical_observations_cannot_disagree_on_an_unregistered_parent(self):
        record, args, _ = self.fixture()
        args["authority"]["target"]["parents"]["/"] = dict(device=1, inode=999)
        args["observation"]["dependencies"][0]["target"]["parents"]["/"] = dict(device=1, inode=10)
        result = self.resolve(record, args)
        self.assertEqual((result.status, result.reason), ("suspended", "dependency_changed"))

    def test_canonical_valid_nested_dependency_parent_chain_is_active(self):
        record, args, identity = self.fixture()
        path = "/repo/shared/guides/guide.md"
        record["shared_policy"][0]["path"] = path
        entry = record["repositories"][0]["checkouts"][0]["clients"][0]
        entry["configuration"]["shared_dependencies"] = [path]
        identity["shared_policy"][0]["path"] = path
        digest = self.digest(identity)
        entry["activation"]["accepted_scope_digest"] = digest
        entry["activation"]["qualification_binding"]["accepted_scope_digest"] = digest
        obs = args["observation"]
        obs["qualification"]["binding"]["accepted_scope_digest"] = digest
        dep = obs["dependencies"][0]
        dep["path"] = dep["target"]["path"] = path
        dep["target"]["parents"].update({"/repo/shared": dict(device=1, inode=20),
                                          "/repo/shared/guides": dict(device=1, inode=30)})
        result = self.resolve(record, args)
        self.assertEqual(result.status, "active")
        self.assertEqual(result.scope.accepted_scope_digest, digest)


    def test_canonical_selected_pins_and_installed_configuration_cannot_be_refreshed(self):
        for change in ("policy", "runtime", "stored-scope", "installed-client"):
            record, args, _ = self.fixture()
            if change == "stored-scope":
                record["repositories"][0]["checkouts"][0]["clients"][0]["activation"]["accepted_scope_digest"] = "0" * 64
            elif change == "installed-client": args["installed"]["clients"] = []
            else: args["installed"][change + "_digest"] = "0" * 64
            with self.subTest(change=change): self.assertEqual(self.resolve(record, args).status, "suspended")


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

    def test_complete_canonical_record_still_rejected_by_production_startup(self):
        record, _, _ = CanonicalScopeTests().fixture()
        path = self.repo / PROJECT_FILE
        path.parent.mkdir()
        path.write_text(json.dumps(record))
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
        (package / "plugin.json").write_text('{"name":"vibe-coding-workflow"}')
        with self.assertRaisesRegex(ValueError, "root manifest hides hooks"):
            validate(package)

    def test_package_validator_rejects_duplicate_claude_hooks(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        shutil.copyfile(package / "hooks/claude-code.json", package / "hooks/hooks.json")
        with self.assertRaisesRegex(ValueError, "duplicate Claude"):
            validate(package)

    def test_package_validator_requires_exact_claude_file_tool_guard(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        path = package / "hooks/claude-code.json"
        original = json.loads(path.read_text())
        for invalid in ("^Edit$", ".*", "^(Edit|Write|NotebookEdit|Bash)$"):
            hooks = json.loads(json.dumps(original))
            hooks["hooks"]["PreToolUse"][0]["matcher"] = invalid
            path.write_text(json.dumps(hooks))
            with self.subTest(matcher=invalid), self.assertRaisesRegex(ValueError, "Claude file-tool"):
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

    def test_pretool_adapter_keeps_native_call_id_without_fabricating_turn(self):
        event = claude_code.normalize_runtime({"hook_event_name": "PreToolUse", "session_id": "session",
            "cwd": str(self.repo), "tool_name": "Edit", "tool_use_id": "native-call"})
        self.assertEqual((event.client, event.tool_name, event.tool_call_id),
                         ("claude-code", "Edit", "native-call"))
        self.assertIsNone(event.turn_id)
        self.assertIsNone(event.receipt_fingerprint)
        for field, value in (("session_id", []), ("cwd", "relative"), ("tool_name", ""),
                             ("tool_use_id", {"id": "native-call"})):
            with self.subTest(field=field), self.assertRaises(StartupError):
                claude_code.normalize_runtime({"hook_event_name": "PreToolUse", "session_id": "session",
                    "cwd": str(self.repo), "tool_name": "Edit", "tool_use_id": "native-call", field: value})
        denied = claude_code.render_runtime(HookResult("PreToolUse", "deny", "Task must be active"))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(claude_code.render_runtime(HookResult("PreToolUse")))


class CanonicalAcquisitionTests(unittest.TestCase):
    setUp = StartupTests.setUp
    git = StartupTests.git

    def record(self, *, project=None, selected=None):
        from runtime import onboarding, state
        project, selected = project or self.repo, selected or self.repo
        observed, error = onboarding._checkout_observation(str(selected), {"paths": 0, "bytes": 0})
        self.assertIsNone(error)
        ref = dict(reference="fixture:standing", sha256="a" * 64)
        config = dict(policy_digest=policy_digest(PACKAGE), runtime_digest=state.runtime_fingerprint(),
            runtime=dict(schema_version=3, state_dir=str(self.base / "private-state" if project == self.repo
                else self.base.parent / (self.base.name + "-state"))),
            shared_dependencies=[], evidence=[ref])
        entry = dict(client="codex", configuration=config, activation=dict(status="configured", qualification="pending",
            accepted_scope_digest=None, transaction_id=None, evidence=[], qualification_binding=None))
        value = dict(schema_version=2, revision=1, project_id="project", project_root=str(project),
            kind="standalone" if project == self.repo else "umbrella", project_binding=onboarding._filesystem_binding(project),
            membership=dict(include=["."], exclude=[], future_join=True, authorization=ref), shared_policy=[],
            repositories=[dict(repository_id="repository", primary_root=observed["primary_root"],
                git_common_dir=observed["git_common_dir"], binding={k: observed["binding"][k] for k in ("primary_root", "git_common_dir")},
                membership="joined", checkouts=[dict(checkout_id="checkout", root=str(selected), git_dir=observed["git_dir"],
                    binding={k: observed["binding"][k] for k in ("root", "git_dir")}, clients=[entry])])])
        path = project / PROJECT_FILE
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value))
        return value, path

    def acquire(self, *, cwd=None, **kwargs):
        from runtime import startup
        event = codex.normalize(dict(self.payload, cwd=str(cwd or self.repo)))
        return startup.acquire_canonical_project(event, PACKAGE, **kwargs)

    def test_shared_membership_acquisition_exposes_observed_authority_without_activation(self):
        from runtime import startup
        value, path = self.record()
        observed = startup._acquire_canonical_membership(self.repo, authority_path=None,
            budget={"paths": 0, "bytes": 0})
        self.assertNotEqual(observed.status, "outside")
        self.assertEqual(observed.authority_path, path)
        self.assertIsNotNone(observed.authority_target)
        self.assertEqual(observed.record.data["project_id"], value["project_id"])
        self.assertEqual(observed.observation["root"], str(self.repo))
        self.assertEqual(observed.observation["membership"], "included")
        self.assertEqual(self.acquire().resolution.reason, "qualification_unobserved")

    def test_actual_standalone_missing_qualification_is_configured_without_writes(self):
        value, path = self.record()
        before = path.read_bytes()
        result = self.acquire()
        self.assertEqual(result.resolution.status, "configured")
        self.assertEqual(result.resolution.reason, "qualification_unobserved")
        self.assertEqual(result.authority_path, path)
        self.assertIsNone(result.resolution.scope)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.base / "private-state").exists())
        with self.assertRaises(StartupError):
            from runtime.startup import resolve_project
            resolve_project(codex.normalize(self.payload), PACKAGE)

    def test_actual_linked_checkout_routes_to_primary_umbrella(self):
        (self.repo / "file").write_text("tracked")
        self.git(self.repo, "add", "file")
        self.git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture")
        linked = self.base / "elsewhere" / "linked"
        self.git(self.repo, "worktree", "add", "--detach", str(linked))
        value, path = self.record(project=self.base, selected=linked)
        seen = []
        result = self.acquire(cwd=linked, qualifier=lambda record, facts: seen.append(facts) or None)
        self.assertEqual(result.resolution.status, "configured")
        self.assertEqual(result.authority_path, path)
        self.assertEqual(seen[0]["observation"]["root"], str(linked))
        self.assertEqual(seen[0]["observation"]["primary_root"], str(self.repo))

    def test_future_member_pending_and_exclusion_outside_do_not_join(self):
        value, path = self.record(project=self.base)
        future = self.base / "future"; future.mkdir(); self.git(future, "init", "-q")
        before = path.read_bytes()
        self.assertEqual(self.acquire(cwd=future).resolution.status, "pending")
        self.assertEqual(path.read_bytes(), before)
        value["membership"]["exclude"] = ["future"]
        path.write_text(json.dumps(value))
        self.assertEqual(self.acquire(cwd=future).resolution.status, "outside")

    def test_overlapping_malformed_and_missing_authorities_fail_closed(self):
        self.record()
        ancestor = self.base / PROJECT_FILE; ancestor.parent.mkdir()
        ancestor.write_text('{"schema_version":2,"schema_version":2}')
        self.assertEqual(self.acquire().resolution.status, "conflict")
        ancestor.unlink()
        self.assertEqual(self.acquire().resolution.status, "conflict")

    def test_selected_dependency_and_runtime_location_are_observed(self):
        import hashlib
        value, path = self.record()
        shared = self.repo / "guide.md"; shared.write_text("accepted guide")
        value["shared_policy"] = [dict(path=str(shared), sha256=hashlib.sha256(shared.read_bytes()).hexdigest())]
        config = value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]
        config["shared_dependencies"] = [str(shared)]
        path.write_text(json.dumps(value))
        self.assertEqual(self.acquire().resolution.status, "configured")
        shared.write_text("changed guide")
        self.assertEqual(self.acquire().resolution.reason, "dependency_changed")
        shared.write_text("accepted guide")
        config["runtime"]["state_dir"] = str(self.repo / "unsafe")
        path.write_text(json.dumps(value))
        self.assertEqual(self.acquire().resolution.reason, "runtime_location_unsafe")

    def activate(self, value, path):
        import hashlib
        from runtime.startup import canonical_scope_projection, decode_project_record, _canonical_plain
        repo = value["repositories"][0]; selected = repo["checkouts"][0]
        entry = selected["clients"][0]
        digest = CanonicalScopeTests.digest(_canonical_plain(canonical_scope_projection(decode_project_record(value),
            repository_id=repo["repository_id"], checkout_id=selected["checkout_id"], client="codex")))
        source = PACKAGE / "hooks/codex.json"
        info = PACKAGE.stat()
        proof = dict(reference="fixture:independent-owner", sha256="e" * 64)
        binding = dict(client="codex", surface="fixture-only", version="1", configuration_digest="f" * 64,
            hook_owner=dict(root=str(PACKAGE), binding=dict(device=info.st_dev, inode=info.st_ino),
                source=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest()),
            accepted_scope_digest=digest, receipt=proof)
        entry["activation"].update(status="active", qualification="qualified", accepted_scope_digest=digest,
            transaction_id="fixture", evidence=[proof], qualification_binding=binding)
        path.write_text(json.dumps(value))
        return dict(current={k: copy.deepcopy(binding[k]) for k in ("client", "surface", "version", "configuration_digest", "hook_owner")},
            receipt=proof, binding=copy.deepcopy(binding), provenance=dict(kind="trusted-coordinator", **proof))

    def test_qualification_requires_independent_internal_owner_and_current_match(self):
        value, path = self.record()
        proof = self.activate(value, path)
        self.assertEqual(self.acquire().resolution.reason, "qualification_unobserved")
        # This callback supplies synthetic trusted-owner fixtures, not native qualification.
        result = self.acquire(qualifier=lambda record, facts: proof)
        self.assertEqual(result.resolution.status, "active")
        self.assertEqual(result.resolution.scope.repository_root, self.repo)
        proof["current"]["surface"] = "changed-surface"
        self.assertEqual(self.acquire(qualifier=lambda record, facts: proof).resolution.reason, "qualification_changed")
        self.assertEqual(self.acquire(qualifier=proof).resolution.status, "conflict")

    def active_shared_dependency(self):
        import hashlib
        value, path = self.record()
        shared = self.repo / "guide.md"; shared.write_text("accepted guide")
        value["shared_policy"] = [dict(path=str(shared), sha256=hashlib.sha256(shared.read_bytes()).hexdigest())]
        config = value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]
        config["shared_dependencies"] = [str(shared)]
        return shared, config, self.activate(value, path)

    def test_selected_dependency_changed_during_qualification_cannot_be_active(self):
        shared, _, proof = self.active_shared_dependency()
        self.assertEqual(self.acquire(qualifier=lambda record, facts: proof).resolution.status, "active")
        for mutation in ("bytes", "same-bytes-replacement", "removed"):
            shared.write_text("accepted guide")
            def change(record, facts):
                if mutation == "bytes":
                    shared.write_text("changed during qualification")
                elif mutation == "same-bytes-replacement":
                    replacement = shared.with_suffix(".copy")
                    replacement.write_bytes(shared.read_bytes()); replacement.replace(shared)
                else:
                    shared.unlink()
                return proof
            with self.subTest(mutation=mutation):
                result = self.acquire(qualifier=change).resolution
                self.assertNotEqual(result.status, "active")
                self.assertIsNone(result.scope)

    def test_runtime_location_changed_during_qualification_cannot_be_active(self):
        _, config, proof = self.active_shared_dependency()
        location = Path(config["runtime"]["state_dir"])
        for mutation in ("repository", "safe-directory", "link"):
            def change(record, facts):
                if mutation == "link":
                    location.symlink_to(self.base, target_is_directory=True)
                else:
                    location.mkdir()
                    if mutation == "repository":
                        self.git(location, "init", "-q")
                return proof
            with self.subTest(mutation=mutation):
                result = self.acquire(qualifier=change).resolution
                self.assertNotEqual(result.status, "active")
                self.assertIsNone(result.scope)
            if location.is_symlink():
                location.unlink()
            else:
                shutil.rmtree(location)

    def test_installed_pins_changed_during_qualification_cannot_be_active(self):
        from runtime import startup, state
        _, _, proof = self.active_shared_dependency()
        for owner, name in ((startup, "policy_digest"), (state, "runtime_fingerprint")):
            original = getattr(owner, name)
            changed = []
            def read(*args):
                return "0" * 64 if changed else original(*args)
            def change(record, facts):
                changed.append(True)
                return proof
            with self.subTest(pin=name), patch.object(owner, name, side_effect=read):
                result = self.acquire(qualifier=change).resolution
                self.assertNotEqual(result.status, "active")
                self.assertIsNone(result.scope)

    def test_post_qualification_observation_keeps_original_deadline(self):
        from runtime import startup
        _, _, proof = self.active_shared_dependency()
        clock = [100.0]
        qualified = []
        original = startup._acquired_runtime_location
        def observe(*args):
            if qualified:
                self.assertEqual(args[-1]["deadline"], 102.0)
                clock[0] = 102.0
            return original(*args)
        def qualify(record, facts):
            self.assertEqual(facts["deadline"], 102.0)
            qualified.append(True)
            clock[0] = 101.5
            return proof
        with patch.object(startup.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(startup, "_acquired_runtime_location", side_effect=observe):
            result = self.acquire(qualifier=qualify, deadline=102.0).resolution
        self.assertNotEqual(result.status, "active")
        self.assertIsNone(result.scope)

    def test_authority_changed_during_same_byte_read_or_qualification_is_rejected(self):
        from runtime import onboarding
        value, path = self.record()
        original = onboarding._read_file
        def substitute(target, *args, **kwargs):
            if Path(target) == path and kwargs.get("expected_binding") is not None:
                replacement = path.with_suffix(".copy")
                replacement.write_bytes(path.read_bytes()); replacement.replace(path)
            return original(target, *args, **kwargs)
        with patch.object(onboarding, "_read_file", side_effect=substitute):
            self.assertEqual(self.acquire().resolution.status, "conflict")
        def change(record, facts):
            value["revision"] += 1; path.write_text(json.dumps(value))
        self.assertEqual(self.acquire(qualifier=change).resolution.reason, "authority_conflict")

    def test_no_enrollment_unrelated_explicit_route_and_schema_one_remain_ineligible(self):
        self.assertEqual(self.acquire().resolution.status, "outside")
        unrelated = self.base / "unrelated"; unrelated.mkdir(); self.git(unrelated, "init", "-q")
        original_repo = self.repo
        self.repo = unrelated
        _, path = self.record()
        self.repo = original_repo
        result = self.acquire(authority_path=path)
        self.assertEqual(result.resolution.status, "outside")
        self.assertIsNone(result.authority_path)
        path = self.repo / PROJECT_FILE; path.parent.mkdir()
        path.write_text(json.dumps(dict(schema_version=1, project_id="old", repository_root=str(self.repo),
            git_common_dir=str(self.repo / ".git"), clients=["codex"], policy_digest=policy_digest(PACKAGE))))
        self.assertEqual(self.acquire().resolution.status, "conflict")

    def test_runtime_bare_cache_link_and_unknown_probe_cannot_become_active(self):
        from runtime import startup
        value, path = self.record()
        proof = self.activate(value, path)
        config = value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]
        initial = config["runtime"]["state_dir"]
        bare = self.base / "bare"; self.git(self.base, "init", "--bare", "-q", str(bare))
        cache = self.base / ".codex/plugins/cache/tasks"
        link = self.base / "state-link"; link.symlink_to(self.base, target_is_directory=True)
        for location in (bare / "tasks", cache, link / "tasks"):
            with self.subTest(location=location):
                config["runtime"]["state_dir"] = str(location)
                path.write_text(json.dumps(value))
                self.assertEqual(self.acquire().resolution.reason, "runtime_location_unsafe")
        config["runtime"]["state_dir"] = initial; path.write_text(json.dumps(value))
        original = startup.read_git
        for exit_code in (1, 128):
            def unknown(root, *args, **kwargs):
                if kwargs.get("discovery"):
                    return (exit_code, b"", b"fatal: invalid configuration\n") if kwargs.get("with_stderr") else (exit_code, b"")
                return original(root, *args, **kwargs)
            with self.subTest(exit_code=exit_code), patch.object(startup, "read_git", side_effect=unknown):
                self.assertEqual(self.acquire(qualifier=lambda record, facts: proof).resolution.reason, "runtime_location_unobserved")

    def test_copied_removed_missing_dependency_and_aggregate_limits_fail_closed(self):
        from runtime import onboarding
        value, path = self.record()
        value["repositories"][0]["membership"] = "removed"; path.write_text(json.dumps(value))
        self.assertEqual(self.acquire().resolution.status, "outside")
        value["repositories"][0]["membership"] = "joined"
        value["project_binding"]["inode"] += 1
        path.write_text(json.dumps(value))
        self.assertEqual(self.acquire().resolution.status, "conflict")
        value["project_binding"]["inode"] -= 1
        missing = str(self.repo / "missing-guide")
        value["shared_policy"] = [dict(path=missing, sha256="a" * 64)]
        value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["shared_dependencies"] = [missing]
        path.write_text(json.dumps(value))
        self.assertEqual(self.acquire().resolution.reason, "dependency_unobserved")
        for limit in ("MAX_PATHS", "MAX_TOTAL"):
            with patch.object(onboarding, limit, 1):
                self.assertEqual(self.acquire().resolution.status, "conflict")

    def test_dependency_descriptor_bytes_cannot_be_rebound_to_replaced_target(self):
        import hashlib
        from runtime import onboarding
        value, path = self.record()
        shared = self.repo / "guide.md"; shared.write_text("accepted")
        value["shared_policy"] = [dict(path=str(shared), sha256=hashlib.sha256(shared.read_bytes()).hexdigest())]
        value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["shared_dependencies"] = [str(shared)]
        proof = self.activate(value, path)
        original = onboarding._physical_target
        changed = []
        def replace_after_target(root, location, *args, **kwargs):
            result = original(root, location, *args, **kwargs)
            if location == shared and not changed:
                replacement = shared.with_suffix(".copy"); replacement.write_text("external changed bytes")
                replacement.replace(shared); changed.append(True)
            return result
        with patch.object(onboarding, "_physical_target", side_effect=replace_after_target):
            self.assertNotEqual(self.acquire(qualifier=lambda record, facts: proof).resolution.status, "active")
        self.assertTrue(changed)

    def test_unrelated_join_preserves_selected_scope_without_sibling_inventory(self):
        from runtime import onboarding
        value, path = self.record(project=self.base)
        proof = self.activate(value, path)
        first = self.acquire(qualifier=lambda record, facts: proof).resolution.scope
        other = self.base / "other"; other.mkdir(); self.git(other, "init", "-q")
        observed, error = onboarding._checkout_observation(str(other), {"paths": 0, "bytes": 0})
        self.assertIsNone(error)
        sibling = copy.deepcopy(value["repositories"][0])
        sibling.update(repository_id="other", primary_root=str(other), git_common_dir=observed["git_common_dir"],
            binding={k: observed["binding"][k] for k in ("primary_root", "git_common_dir")})
        selected = sibling["checkouts"][0]
        selected.update(checkout_id="other-checkout", root=str(other), git_dir=observed["git_dir"],
            binding={k: observed["binding"][k] for k in ("root", "git_dir")})
        selected["clients"][0]["activation"] = dict(status="configured", qualification="pending",
            accepted_scope_digest=None, transaction_id=None, evidence=[], qualification_binding=None)
        value["repositories"].append(sibling); value["revision"] += 1; path.write_text(json.dumps(value))
        with patch.object(onboarding, "_snapshot", side_effect=AssertionError("proposal inventory was loaded")),              patch.object(onboarding, "_locked", side_effect=AssertionError("startup wrote private plan state")):
            second = self.acquire(qualifier=lambda record, facts: proof).resolution.scope
        self.assertEqual(first.accepted_scope_digest, second.accepted_scope_digest)

    def test_valid_overlapping_authorities_are_not_chosen_by_nearest_ancestry(self):
        self.record(project=self.base)
        self.record()
        self.assertEqual(self.acquire().resolution.reason, "authority_conflict")

    def test_git_diagnostics_are_bounded_and_locale_controlled_without_changing_default_shape(self):
        from runtime import startup
        original = startup.subprocess.Popen
        with patch.object(startup.subprocess, "Popen", wraps=original) as process,              patch.dict(os.environ, {"LC_ALL": "invalid-fixture-locale", "GIT_DIR": "/invalid"}):
            code, raw, diagnostic = startup.read_git(self.base, "rev-parse", "--absolute-git-dir",
                discovery=True, with_stderr=True)
        self.assertEqual(code, 128)
        self.assertFalse(raw)
        self.assertEqual(diagnostic, b"fatal: not a git repository (or any of the parent directories): .git\n")
        environment = process.call_args.kwargs["env"]
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertNotIn("GIT_DIR", environment)
        self.assertIn("--no-optional-locks", process.call_args.args[0])
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        with self.assertRaisesRegex(StartupError, "Git inventory limit"):
            startup.read_git(self.repo, "--invalid-fixture-option", with_stderr=True, limit=1)
        self.assertEqual(len(startup.read_git(self.repo, "--version")), 2)

    def test_dependency_same_inode_changed_after_descriptor_read_cannot_be_active(self):
        import hashlib
        from runtime import onboarding
        value, path = self.record()
        shared = self.repo / "guide.md"; shared.write_text("accepted")
        value["shared_policy"] = [dict(path=str(shared), sha256=hashlib.sha256(shared.read_bytes()).hexdigest())]
        value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["shared_dependencies"] = [str(shared)]
        proof = self.activate(value, path)
        original = onboarding._read_file
        inode = shared.stat().st_ino
        def change_after_read(target, *args, **kwargs):
            raw = original(target, *args, **kwargs)
            if Path(target) == shared and kwargs.get("expected_binding") is not None:
                shared.write_text("changed in place after read")
            return raw
        with patch.object(onboarding, "_read_file", side_effect=change_after_read):
            self.assertNotEqual(self.acquire(qualifier=lambda record, facts: proof).resolution.status, "active")
        self.assertEqual(shared.stat().st_ino, inode)

    def test_unsupported_candidate_skips_legacy_runtime_probe_without_changing_d3_default(self):
        from runtime import onboarding
        path = self.repo / PROJECT_FILE; path.parent.mkdir()
        legacy = dict(schema_version=1, project_id="legacy", repository_root=str(self.repo),
            git_common_dir=str(self.repo / ".git"), clients=["codex"], policy_digest=policy_digest(PACKAGE),
            runtime=dict(schema_version=2, state_dir=str(self.base / "legacy-state")))
        path.write_text(json.dumps(legacy))
        with patch.object(onboarding, "runtime_settings", side_effect=AssertionError("legacy probing loaded")):
            self.assertEqual(self.acquire().resolution.status, "conflict")
        with patch.object(onboarding, "runtime_settings", return_value=None) as probe:
            self.assertIsNone(onboarding._authority_observation(self.repo, {"paths": 0, "bytes": 0})["error"])
            probe.assert_called_once()

    def test_ineligible_comparison_does_not_invoke_qualification(self):
        value, path = self.record(project=self.base)
        future = self.base / "future"; future.mkdir(); self.git(future, "init", "-q")
        qualifier = unittest.mock.Mock(side_effect=AssertionError("qualification does not apply"))
        self.assertEqual(self.acquire(cwd=future, qualifier=qualifier).resolution.status, "pending")
        value["membership"]["exclude"] = ["project"]; path.write_text(json.dumps(value))
        self.assertEqual(self.acquire(qualifier=qualifier).resolution.status, "outside")
        value["membership"]["exclude"] = []
        value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["runtime_digest"] = "0" * 64
        path.write_text(json.dumps(value))
        self.assertEqual(self.acquire(qualifier=qualifier).resolution.status, "suspended")
        qualifier.assert_not_called()

    def test_expired_root_probe_cannot_report_proven_outside(self):
        from runtime import startup
        clock = [100.0]
        def expired(cwd, **kwargs):
            clock[0] = 104.0
            return None
        with patch.object(startup.time, "monotonic", side_effect=lambda: clock[0]),              patch.object(startup, "repository_root", side_effect=expired):
            self.assertEqual(self.acquire().resolution.status, "conflict")

    def test_one_monotonic_deadline_limits_total_git_and_rejects_late_qualification(self):
        from runtime import startup
        value, path = self.record()
        clock = [100.0]
        original = startup.read_git
        calls = []
        def delayed(root, *args, **kwargs):
            remaining = kwargs.get("deadline", clock[0] + kwargs.get("timeout", 5)) - clock[0]
            calls.append(remaining)
            if remaining <= 1.5:
                clock[0] += max(0, remaining)
                raise subprocess.TimeoutExpired("bounded fixture", remaining)
            result = original(root, *args, **kwargs)
            clock[0] += 1.5
            return result
        with patch.object(startup.time, "monotonic", side_effect=lambda: clock[0]),              patch.object(startup, "read_git", side_effect=delayed):
            self.assertEqual(self.acquire().resolution.status, "conflict")
        self.assertLessEqual(clock[0] - 100, 4)
        self.assertLessEqual(len(calls), 3)
        proof = self.activate(value, path)
        clock[0] = 100
        def late(record, facts):
            self.assertIn("deadline", facts)
            clock[0] = facts["deadline"] + 0.1
            return proof
        with patch.object(startup.time, "monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(self.acquire(qualifier=late).resolution.status, "conflict")

    def test_shared_git_reader_is_bounded_and_d3_uses_it(self):
        from runtime import onboarding, startup
        with self.assertRaisesRegex(StartupError, "Git inventory limit"):
            startup.read_git(self.repo, "--version", limit=1)
        with patch.object(startup, "read_git", return_value=(0, b"fixture")) as reader:
            self.assertEqual(onboarding._git(self.repo, "status"), (0, b"fixture"))
            reader.assert_called_once()
        env = dict(GIT_DIR="/invalid", GIT_WORK_TREE="/invalid", GIT_COMMON_DIR="/invalid")
        with patch.dict(os.environ, env):
            self.assertEqual(checkout(self.repo)[0], self.repo)


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
        self.family=self.fixture.base/'.vibe-coding-workflow/workstreams-families.sqlite3'

    def reserve(self,home=None):
        from scripts.workstreams_family import reserve
        path=(home or Path.home())/'.vibe-coding-workflow/workstreams-families.sqlite3'
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
