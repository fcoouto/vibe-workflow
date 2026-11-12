"""Exercise the protected evaluator and portable, retained project inputs."""

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.fixture_package import copy_package
from scripts.validate import validate


PACKAGE = Path(__file__).resolve().parents[1]
ASSETS = PACKAGE / "assets/harness-evaluation"
EVALUATOR = PACKAGE / "scripts/validate-harness.mjs"
CHECKS = (
    "allows the owner in the same tenant",
    "denies another user in the same tenant",
    "denies matching user IDs across tenants",
    "denies absent actors and records",
    "denies missing, empty, or non-string identities",
    "does not mutate its inputs",
    "preserves the local customization",
)
BASELINE_FAILURES = {CHECKS[2], CHECKS[4]}
CORRECT_CONTROL = """export function canReadRecord(actor, record) {
  if (!actor || !record) return false;
  const identities = [actor.id, actor.tenantId, record.ownerId, record.tenantId];
  return identities.every(value => typeof value === "string" && value.length > 0)
    && actor.id === record.ownerId && actor.tenantId === record.tenantId;
}
"""
PHASE_TWO_HASHES = {
    "minimal/input/README.md": "c92295bdaf64c404d326d54c904f3643aa04ccdd2cb4fab25b65c48d16aa69ab",
    "minimal/input/AGENTS.md": "88491d96a5150826761c5ff8b4fd5c257aa71c49b998cfbfef542da29855abcb",
    "minimal/input/arithmetic.py": "ba1a531f581d2e6094e978ed6f7aca7a8d92eeb62c6e7ad73ee692f7f18bc772",
    "minimal/input/check.py": "6d74e0d06e25072e5a4cab173259d26ff98decfd7273a80629a16c38f0fc515d",
    "minimal/expected-final/README.md": "09975916c4ab910a5f8340a38706ab092975611c6468ada23785594fc22d82f4",
    "minimal/expected-final/AGENTS.md": "fd4a9e0f73ef25f39dcb5fce81cf8faa7b575d5f4989e31e3d50471041e05578",
    "customized/input/README.md": "733d9f24dd61d67fff808aaf1ba18c9f7b579473005c04cf11f5cd4b269c16ea",
    "customized/input/AGENTS.md": "1d3ce032b1e55550e434ec4b45024bb03ac99f6b5cae5a2f822718d0bae10bca",
    "customized/input/handbook/overview.md": "f3cb7ac4a375b45517c0d8a6a04e8eaeeba65a2d758947aaebcc868cd6f69f82",
    "customized/input/handbook/engineering.md": "dc0c71a7ddf439a3f1d030af50f89b552b53252e24552dd6abd197e1954cd855",
    "customized/input/handbook/ARCHITECTURE.md": "628f1ed6eff7b01049e48b42bd4088ee3aaba142082ef1ae7334752e84965719",
    "customized/input/src/AGENTS.md": "f179e05f41fd0e510d27360fd83856f346b4153e69d2dd423a49f5b62fda9307",
    "customized/input/src/cleaning.py": "ca96808deffc5b0c86fe9d113cf8fa311221421c4ef7026c9a3c61a100c495ff",
    "customized/input/check.py": "6d322f74ecaf115b012b9b432b8a157a48bc20a755043c1f203ebde8cee84db8",
    "customized/expected-after-partial-undo/handbook/engineering.md": "a2a846de4c7bac7c8fe45f949c948974c37c256520246b415374bab75894742a",
}


class HarnessEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if cls.node is None:
            raise RuntimeError("Node is unavailable; harness evaluation cannot run")

    def setUp(self):
        self.assertTrue(EVALUATOR.is_file(), "Packaged Node evaluator is missing")
        self.temp = tempfile.TemporaryDirectory(prefix="harness evaluation ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / "candidate"
        shutil.copytree(ASSETS / "boundary", self.fixture)
        self.original = self.snapshot(ASSETS / "boundary")

    def snapshot(self, directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def run_evaluator(self, evaluator=EVALUATOR, arguments=None, cwd=None):
        result = subprocess.run(
            [self.node, "--test-reporter=tap", str(evaluator),
             *(arguments if arguments is not None else [str(self.fixture)])],
            cwd=cwd or self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(self.snapshot(ASSETS / "boundary"), self.original)
        return result

    def assert_checks(self, result, failures):
        output = result.stdout + result.stderr
        checks = re.findall(r"^(ok|not ok) \d+ - (.+)$", output, re.MULTILINE)
        expected = [("not ok" if name in failures else "ok", name) for name in CHECKS]
        self.assertEqual(checks, expected, output)
        if failures:
            self.assertNotEqual(result.returncode, 0, output)
        else:
            self.assertEqual(result.returncode, 0, output)

    def test_broken_baseline_has_only_expected_failures(self):
        before = self.snapshot(self.fixture)
        self.assert_checks(self.run_evaluator(), BASELINE_FAILURES)
        self.assertEqual(self.snapshot(self.fixture), before)

    def test_correct_control_passes_all_seven(self):
        (self.fixture / "access.mjs").write_text(CORRECT_CONTROL)
        self.assert_checks(self.run_evaluator(), set())

    def test_changed_notes_fail_preservation(self):
        (self.fixture / "access.mjs").write_text(CORRECT_CONTROL)
        (self.fixture / "local-notes.txt").write_text("Changed candidate notes\n")
        self.assert_checks(self.run_evaluator(), {CHECKS[6]})

    def test_relocated_package_uses_protected_notes(self):
        package = self.root / "relocated package"
        (package / "scripts").mkdir(parents=True)
        evaluator = package / "scripts/validate-harness.mjs"
        shutil.copy2(EVALUATOR, evaluator)
        reference = package / "assets/harness-evaluation/boundary"
        shutil.copytree(ASSETS / "boundary", reference)
        before = self.snapshot(reference)
        cwd = self.root / "unrelated cwd"
        cwd.mkdir()
        self.assert_checks(self.run_evaluator(evaluator, cwd=cwd), BASELINE_FAILURES)
        (self.fixture / "access.mjs").write_text(CORRECT_CONTROL)
        (self.fixture / "local-notes.txt").write_text("Worker-controlled notes\n")
        self.assert_checks(self.run_evaluator(evaluator, cwd=cwd), {CHECKS[6]})
        self.assertEqual(self.snapshot(reference), before)

    def test_missing_argument_or_fixture_is_rejected_without_rewriting_inputs(self):
        before = self.snapshot(self.fixture)
        for arguments in ([], [str(self.root / "missing fixture")]):
            with self.subTest(arguments=arguments):
                result = self.run_evaluator(arguments=arguments)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(self.fixture), before)

    def test_adopted_baseline_notes_evaluator_and_license_keep_their_identity(self):
        expected = {
            ASSETS / "boundary/access.mjs": "fa96fe6a759bc49db77236810a83bb4c97d3ce693124bb6505545e0193550240",
            ASSETS / "boundary/local-notes.txt": "79e3fc6afaf135133c764bc261b5040575c373a80dcb44b254bce07e5a4a5b0c",
            ASSETS / "LICENSE": "d687e4265813cc684a8307b579a68ac19578f3de351aafbd6419da926b52203f",
            EVALUATOR: "6b14b8a256d7c793006fc5a64ec4aac4842ad301e025f210ad25f5f6d6bb1113",
        }
        for path, digest in expected.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_retained_project_inputs_and_overlays_match_sanitized_snapshots(self):
        source = ASSETS / "phase-two"
        self.assertEqual(set(self.snapshot(source)), set(PHASE_TWO_HASHES))
        for name, digest in PHASE_TWO_HASHES.items():
            with self.subTest(path=name):
                self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest)

    def test_retained_projects_and_document_overlays_keep_accepted_checks_passing(self):
        for name, overlay in (("minimal", "expected-final"),
                              ("customized", "expected-after-partial-undo")):
            source = ASSETS / "phase-two" / name
            project = self.root / name
            shutil.copytree(source / "input", project)
            for state in ("initial", overlay):
                with self.subTest(project=name, state=state):
                    if state != "initial":
                        shutil.copytree(source / overlay, project, dirs_exist_ok=True)
                    result = subprocess.run([sys.executable, "-B", "check.py"], cwd=project,
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_package_validation_rejects_missing_harness_assets(self):
        package = self.root / "package"
        copy_package(PACKAGE, package, ".superpowers")
        # A valid package carries no instruction map; validation reads every instruction source directly.
        (package / "clients/instruction-map.json").unlink(missing_ok=True)
        self.assertFalse((package / "clients/instruction-map.json").exists())
        self.assertEqual(validate(package)["validation"], "passed")
        self.assertFalse((package / "clients/instruction-map.json").exists(), "validation must not write a map")
        required = ["scripts/validate-harness.mjs", "assets/harness-evaluation/LICENSE",
                    "assets/harness-evaluation/cases.md", "assets/harness-evaluation/boundary/AGENTS.md",
                    "assets/harness-evaluation/boundary/access.mjs",
                    "assets/harness-evaluation/boundary/local-notes.txt"]
        required += ["assets/harness-evaluation/phase-two/" + name for name in PHASE_TWO_HASHES]
        for name in required:
            with self.subTest(path=name):
                path = package / name
                content = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises((OSError, ValueError)):
                        validate(package)
                finally:
                    path.write_bytes(content)


if __name__ == "__main__":
    unittest.main()
