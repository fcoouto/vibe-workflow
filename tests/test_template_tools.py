"""Read-only template assistance: consumer context, preservation and bounded feedback."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/template_tools.py"


class TemplateToolsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime.template_tools"),
                             "Read-only template helper is not implemented")
        from runtime import template_tools
        self.tools = template_tools
        self.temp = tempfile.TemporaryDirectory(prefix="template tools ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "consumer"
        self.root.mkdir()
        self.before = self.base / "before"
        self.before.mkdir()

    def write(self, name, text, root=None):
        path = (root or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def codes(self, result):
        return {f["code"] for f in result["findings"]}

    def test_prepare_delivers_only_explicit_shapes_and_observed_context(self):
        path = self.write("AGENTS.md", "# Original rules\n")
        result = self.tools.prepare(self.root, ["project/product.md", "project/product.md"], ["AGENTS.md"])
        self.assertEqual(result["context"], {"AGENTS.md": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.assertEqual([s["path"] for s in result["instructions"]["sections"]],
                         ["templates/project/product.md"])
        self.assertEqual(result["instructions"]["sections"][0]["text"],
                         (PACKAGE / "templates/project/product.md").read_text())
        self.assertRegex(result["observed_at"], r"^\d{4}-\d\d-\d\dT")
        self.assertFalse((self.root / ".vibe-workflow").exists())

    def test_prepare_rejects_missing_consumer_context_even_when_producer_has_it(self):
        self.write(".workflow/OWNER.md", "# Owner\n", self.before)
        with self.assertRaisesRegex(ValueError, "context_unavailable"):
            self.tools.prepare(self.root, ["worker-brief.md"], [".workflow/OWNER.md"])

    def test_prepare_reads_current_templates_without_map_and_binds_revision(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE / "templates", package / "templates")
        template = package / "templates/project/product.md"
        template.write_text("# Current product\n")
        with patch.object(self.tools, "PACKAGE", package):
            first = self.tools.prepare(self.root, ["project/product.md"], [])
            self.assertEqual(first["instructions"]["sections"][0]["text"], "# Current product\n")
            template.write_text("# Updated product\n")
            with self.assertRaisesRegex(ValueError, "context_changed"):
                self.tools.prepare(self.root, ["project/product.md"], [], revision=first["revision"])
            current = self.tools.prepare(self.root, ["project/product.md"], [])
            self.assertEqual(current["instructions"]["sections"][0]["text"], "# Updated product\n")
            self.assertNotEqual(current["revision"], first["revision"])
        self.assertFalse((package / "clients/instruction-map.json").exists())

    def test_prepare_revision_binds_consumer_context(self):
        path = self.write("rules.md", "original")
        first = self.tools.prepare(self.root, ["project/product.md"], ["rules.md"])
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "context_changed"):
            self.tools.prepare(self.root, ["project/product.md"], ["rules.md"], revision=first["revision"])

    def test_duplicate_documents_are_checked_once_and_bad_roots_fail(self):
        self.write("README.md", "[bad](missing.md)\n")
        result = self.tools.check(self.root, ["README.md", "README.md"])
        self.assertEqual(len(result["findings"]), 1)
        with self.assertRaisesRegex(ValueError, "root_unavailable"):
            self.tools.check(self.base / "absent", [])

    def test_changed_link_content_invalidates_notice_fingerprint(self):
        self.write("README.md", "[bad](owner.md#absent)\n")
        target = self.write("owner.md", "# Owner\n")
        first = self.tools.check(self.root, ["README.md"])
        target.write_text("# New owner\n")
        second = self.tools.check(self.root, ["README.md"])
        self.assertTrue(self.tools.notice(second, previous=first["fingerprint"]))

    def test_required_digest_checks_the_actual_consumer_copy(self):
        path = self.write("rules.md", "original")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertTrue(self.tools.check(self.root, [], required={"rules.md": digest})["ok"])
        path.write_text("changed")
        result = self.tools.check(self.root, [], required={"rules.md": digest, "missing.md": digest})
        self.assertEqual(self.codes(result), {"context_changed", "context_unavailable"})

    def test_combined_documents_need_no_git_or_nine_file_layout(self):
        self.write("README.md", "# Small project\n\n## Run\nUse Python.\n")
        result = self.tools.check(self.root, ["README.md"], ["project/overview.md", "project/product.md"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.tools.notice(result), "")
        self.assertIn("semantic", result["limits"])

    def test_preservation_counts_original_lines_but_allows_reordering(self):
        original = "# Notes\nKeep this.\nKeep this.\nOmit lines that do not apply; never invent values.\n"
        self.write("README.md", original, self.before)
        path = self.write("README.md", "# Notes\nOmit lines that do not apply; never invent values.\nKeep this.\nKeep this.\nNew.\n")
        result = self.tools.check(self.root, ["README.md"], ["setup-record.md"], before=self.before)
        self.assertTrue(result["ok"], result)
        path.write_text("# Notes\nKeep this.\nOmit lines that do not apply; never invent values.\n")
        self.assertIn("original_line_removed", self.codes(self.tools.check(
            self.root, ["README.md"], ["setup-record.md"], before=self.before)))

    def test_only_added_template_residue_is_reported_and_never_removed(self):
        self.write("SETUP.md", "# Setup\nOriginal [x] checkbox.\n", self.before)
        text = "# Setup\nOriginal [x] checkbox.\nOmit lines that do not apply; never invent values.\n- Project: [name, repository, remote, primary branch and verification date]\n"
        path = self.write("SETUP.md", text)
        result = self.tools.check(self.root, ["SETUP.md"], ["setup-record.md"], before=self.before)
        self.assertEqual(self.codes(result), {"author_note", "placeholder"})
        self.assertEqual(path.read_text(), text)
        self.assertNotIn("Original [x]", json.dumps(result))

    def test_local_links_resolve_from_destination_and_check_heading_anchors(self):
        self.write("AGENTS.md", "# Team Rules\n\n## Café\n\n## Team Rules\n")
        path = self.write("handbook/setup.md", "[rules](../AGENTS.md#team-rules)\n[café](../AGENTS.md#caf%C3%A9)\n[duplicate](../AGENTS.md#team-rules-1)\n[external](https://example.invalid/no-fetch)\n")
        self.assertTrue(self.tools.check(self.root, ["handbook/setup.md"])["ok"])
        path.write_text("[bad](AGENTS.md)\n[anchor](../AGENTS.md#absent)\n")
        self.assertEqual(self.codes(self.tools.check(self.root, ["handbook/setup.md"])),
                         {"link_missing", "anchor_missing"})

    def test_reference_links_and_code_examples_do_not_create_false_failures(self):
        self.write("Some File.md", "# Hello\n")
        self.write("README.md", '[valid][guide]\n\n[guide]: <Some%20File.md#hello> "Title"\n\n`[code](missing.md)`\n\n```md\n[example](missing.md)\n```\n')
        self.assertTrue(self.tools.check(self.root, ["README.md"])["ok"])

    def test_heading_anchors_preserve_visible_inline_code(self):
        path = self.write("README.md", "# Run `tool`\n\n[run](#run-tool)\n")
        self.assertTrue(self.tools.check(self.root, ["README.md"])["ok"])
        path.write_text("# Run `tool`\n\n[absent](#run-other)\n")
        self.assertEqual(self.codes(self.tools.check(self.root, ["README.md"])), {"anchor_missing"})

    def test_duplicate_reference_labels_use_first_definition(self):
        self.write("present.md", "# Present\n")
        for first, second, expected in (("missing.md", "present.md", {"link_missing"}),
                                        ("present.md", "missing.md", set())):
            with self.subTest(first=first):
                self.write("README.md", f"[guide][r guide]\n\n[R  Guide]: {first}\n[r guide]: {second}\n")
                self.assertEqual(self.codes(self.tools.check(self.root, ["README.md"])), expected)

    def test_duplicate_heading_collisions_and_repeated_links_finish_within_budget(self):
        self.write("collisions.md", "# a\n# a-1\n# a\n# a-1\n# a\n")
        self.write("large.md", "# a\n" * 20000)
        links = "".join(f"[collision](collisions.md#{anchor})\n"
                        for anchor in ("a", "a-1", "a-2", "a-1-1", "a-3"))
        self.write("README.md", links + "[last](large.md#a-19999)\n" * 100)
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), "check", "--cwd", str(self.root),
             "--document", "README.md", "--quiet"],
            capture_output=True, text=True, timeout=5,
            env={"PATH": os.defpath, "HOME": str(self.base)})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_selected_paths_refuse_traversal_symlinks_and_special_files(self):
        outside_text = "outside-file-content-must-not-be-returned"
        self.write("outside.md", outside_text, self.base)
        (self.root / "alias.md").symlink_to(self.base / "outside.md")
        (self.root / "directory").symlink_to(self.base, target_is_directory=True)
        os.mkfifo(self.root / "fifo.md")
        for name in ("../outside.md", "alias.md", "directory/outside.md", "fifo.md", str(self.base / "outside.md")):
            with self.subTest(name=name):
                result = self.tools.check(self.root, [name])
                self.assertFalse(result["ok"])
                self.assertEqual(self.codes(result), {"path_unsafe"})
                self.assertNotIn(outside_text, json.dumps(result))
        with self.assertRaises(ValueError):
            self.tools.prepare(self.root, ["../policies/00-index.md"], [])

    def test_link_escapes_and_symlink_targets_are_not_read(self):
        self.write("secret.md", "# Private", self.base)
        (self.root / "alias.md").symlink_to(self.base / "secret.md")
        self.write("README.md", "[escape](../secret.md)\n[alias](alias.md#private)\n")
        result = self.tools.check(self.root, ["README.md"])
        self.assertEqual(self.codes(result), {"link_unsafe"})

    def test_path_and_byte_limits_fail_without_truncation(self):
        path = self.write("large.md", "a" * (1024 * 1024 + 1))
        self.assertIn("file_too_large", self.codes(self.tools.check(self.root, [path.name])))
        with self.assertRaisesRegex(ValueError, "selection_limit"):
            self.tools.check(self.root, [f"{i}.md" for i in range(65)])
        for i in range(5):
            self.write(f"{i}.md", "a" * (1024 * 1024))
        result = self.tools.check(self.root, [f"{i}.md" for i in range(5)])
        self.assertIn("operation_too_large", self.codes(result))

    def test_notice_is_bounded_deduplicated_and_invalidates_on_content_change(self):
        path = self.write("README.md", "[bad](missing.md)\n")
        first = self.tools.check(self.root, ["README.md"])
        self.assertIn("link_missing", self.tools.notice(first))
        self.assertLessEqual(len(self.tools.notice(first)), 1000)
        self.assertEqual(self.tools.notice(first, previous=first["fingerprint"]), "")
        path.write_text("[bad](another.md)\n")
        second = self.tools.check(self.root, ["README.md"])
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertTrue(self.tools.notice(second, previous=first["fingerprint"]))

    def test_no_baseline_never_claims_preservation(self):
        self.write("README.md", "# New document\n")
        result = self.tools.check(self.root, ["README.md"])
        self.assertEqual(result["preservation"], "not_checked")

    def test_bad_baseline_is_not_treated_as_new_document(self):
        self.write("README.md", "# Notes\n")
        (self.before / "README.md").symlink_to(self.root / "README.md")
        result = self.tools.check(self.root, ["README.md"], before=self.before)
        self.assertIn("baseline_unavailable", self.codes(result))

    def test_cli_quiet_success_and_fixed_json_errors(self):
        self.write("README.md", "# Project\n")
        command = [sys.executable, "-I", "-B", str(SCRIPT), "check", "--cwd", str(self.root)]
        result = subprocess.run(command + ["--document", "README.md", "--quiet"], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        result = subprocess.run(command + ["--require", "README.md=secret-not-a-digest"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertNotIn("secret-not-a-digest", result.stdout + result.stderr)

    def test_protected_project_replay_rejects_residue_links_and_missing_transport(self):
        fixture = PACKAGE / "assets/harness-evaluation/phase-two/customized/input"
        protected = {path.relative_to(fixture): path.read_bytes()
                     for path in fixture.rglob("*") if path.is_file()}
        shutil.copytree(fixture, self.root, dirs_exist_ok=True)
        shutil.copytree(fixture, self.before, dirs_exist_ok=True)
        original = (self.root / "README.md").read_text()
        self.assertTrue(self.tools.check(self.root, ["README.md"], before=self.before)["ok"])
        path = self.write("README.md", original + "\nOmit lines that don't apply; never invent values.\n[missing](handbook/missing.md)\n")
        result = self.tools.check(self.root, ["README.md"], ["setup-record.md"], before=self.before)
        self.assertEqual(self.codes(result), {"author_note", "link_missing"})
        path.write_text(original)
        self.assertTrue(self.tools.check(self.root, ["README.md"], ["setup-record.md"], before=self.before)["ok"])
        expected = hashlib.sha256(protected[Path("handbook/overview.md")]).hexdigest()
        required = {"handbook/overview.md": expected}
        (self.root / "handbook/overview.md").unlink()
        self.assertIn("context_unavailable", self.codes(self.tools.check(self.root, [], required=required)))
        (self.root / "handbook/overview.md").write_bytes(protected[Path("handbook/overview.md")])
        self.assertTrue(self.tools.check(self.root, [], required=required)["ok"])
        for name, raw in protected.items():
            self.assertEqual((fixture / name).read_bytes(), raw)
            self.assertEqual((self.root / name).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
