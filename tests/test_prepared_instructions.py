"""Prepared delivery must preserve exact instructions, scope and revisions."""

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/read_instructions.py"


class PreparedInstructionsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Prepared instruction reader is missing")
        spec = importlib.util.spec_from_file_location("prepared_reader", SCRIPT)
        self.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reader)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "clients").mkdir()
        (self.root / "policies").mkdir()
        (self.root / "clients/codex.md").write_text("# Client\nRead applicable contracts.\n")
        self.source = "# Rules\n" + "Preserve Straße and 漢字 exactly.\n" * 900
        (self.root / "policies/rules.md").write_text(self.source)
        (self.root / "policies/unrelated.md").write_text("Do not load this unused procedure.\n")
        self.prepare()

    def prepare(self):
        manifest = self.reader.build_manifest(self.root)
        (self.root / "clients/instruction-map.json").write_text(json.dumps(manifest))

    def external_file(self, text, name="Ü skill.md"):
        directory = tempfile.TemporaryDirectory(prefix="external instructions ")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name).resolve() / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_auto_cli_mixed_sources_keep_package_checks_and_exclusive_flags(self):
        external = self.external_file("# External\r\nKeep 😀 and every rule.\r\n")
        package = PACKAGE / "clients/workstreams-titles.md"
        command = [sys.executable, "-I", "-B", str(SCRIPT)]
        result = subprocess.run(command + ["--auto", "--json", "--batch", str(external), str(package)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(packet["mode"], "auto")
        self.assertFalse(packet["external"])
        self.assertEqual(packet["characters"], len(packet["text"]))
        self.assertIn(external.read_bytes().decode(), packet["text"])
        sections = re.findall(rf"\n--- {re.escape(str(package))} lines \d+-\d+ ---\n(.*?)\n--- source (?:complete|continues) ---\n",
                              packet["text"], re.S)
        self.assertEqual("".join(sections), package.read_text())
        self.assertLess(packet["text"].index(str(external)), packet["text"].index(str(package)))
        for flags in (["--auto", "--external"], ["--auto", "--helper"], ["--auto", "--prepare"]):
            rejected = subprocess.run(command + flags, capture_output=True, timeout=5)
            self.assertNotEqual(rejected.returncode, 0, flags)
            self.assertEqual(rejected.stdout, b"")

    def test_auto_batches_reconstruct_mixed_sources_in_order_with_bounded_fallback(self):
        external = self.external_file(self.source.replace("\n", "\r\n"))
        paths = ["clients/codex.md", str(external), str(self.root / "policies/unrelated.md")]
        expected = ["# Client\nRead applicable contracts.\n", external.read_bytes().decode(),
                    "Do not load this unused procedure.\n"]
        first = self.reader.load_batch(self.root, paths, auto=True)
        recovered = dict.fromkeys(paths, "")
        batch = first
        while True:
            rendered = self.reader.render_page(batch)
            self.assertLessEqual(len(rendered.encode()), 24000)
            for section in batch["sections"]:
                recovered[section["path"]] += section["text"]
            page = batch["last_page"] + 1
            if page > batch["pages"]:
                break
            self.assertIn(f"--auto --batch --page {page} --revision {first['revision']}", rendered)
            batch = self.reader.load_batch(self.root, paths, page, first["revision"], auto=True)
        self.assertEqual(list(recovered.values()), expected)
        fallback = self.reader.load_page(self.root, paths, first["last_page"] + 1, first["revision"], auto=True)
        self.assertLessEqual(len(self.reader.render_page(fallback).encode()), 9500)
        part = fallback["sections"][0]
        self.assertEqual(part["start_line"], first["sections"][-1]["end_line"] + 1)

    def test_auto_preserves_absolute_alias_manifest_authority(self):
        alias = self.root / "policies/alias.md"
        alias.symlink_to("rules.md")
        self.prepare()
        outside = self.external_file("# Outside\n")
        directory_alias = outside.parent / "package"
        directory_alias.symlink_to(self.root, target_is_directory=True)
        outside_alias = outside.parent / "package-source.md"
        outside_alias.symlink_to(self.root / "policies/rules.md")
        for name in (str(alias), str(directory_alias / "policies/alias.md"), str(outside_alias)):
            self.assertIn("Preserve Straße", self.reader.render_page(self.reader.load_page(self.root, [name], auto=True)))
        manifest_path = self.root / "clients/instruction-map.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["policies/alias.md"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        for name in ("policies/alias.md", str(alias), str(directory_alias / "policies/alias.md")):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Prepared source changed"):
                self.reader.load_page(self.root, [name], auto=True)
        (self.root / "policies/rules.md").write_text("# Changed package source\n")
        with self.assertRaisesRegex(ValueError, "Prepared source changed"):
            self.reader.load_page(self.root, [str(outside_alias)], auto=True)

    def test_auto_rejects_package_origin_escapes_before_reading_external_bytes(self):
        outside = self.root.parent / (self.root.name + "-outside.md")
        outside.write_text("# External bytes must not bypass package authority\n")
        self.addCleanup(outside.unlink)
        escaped = self.root / "policies/escape.md"
        escaped.symlink_to(outside)
        alias_parent = self.external_file("# Alias parent\n").parent
        alias = alias_parent / "package"
        alias.symlink_to(self.root, target_is_directory=True)
        reads = Counter()
        original = Path.read_bytes
        def observe(path):
            reads[path] += 1
            return original(path)
        with patch.object(Path, "read_bytes", observe):
            for name in ("policies/escape.md", str(escaped), str(alias / "policies/escape.md"),
                         str(alias / ".." / outside.name)):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.reader.load_batch(self.root, [name], auto=True)
        self.assertNotIn(outside.resolve(), reads)

    def test_auto_revision_binds_mode_target_authority_and_order(self):
        external = self.external_file(self.source)
        other = self.external_file(self.source)
        alias = external.parent / "alias.md"
        alias.symlink_to(external)
        paths = [str(alias)]
        first = self.reader.load_batch(self.root, paths, auto=True)
        page = first["last_page"] + 1
        self.assertLessEqual(page, first["pages"])
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, page, first["revision"], external=True)
        legacy = self.reader.load_batch(self.root, paths, external=True)
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, page, legacy["revision"], auto=True)
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, ["clients/codex.md"] + paths, page, first["revision"], auto=True)
        for target in (other, self.root / "policies/rules.md"):
            alias.unlink(); alias.symlink_to(target)
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.reader.load_batch(self.root, paths, page, first["revision"], auto=True)

    def test_auto_external_selection_reads_once_without_a_package_manifest(self):
        external = self.external_file("# Small external\n")
        (self.root / "clients/instruction-map.json").unlink()
        reads = Counter()
        original = Path.read_bytes
        def observe(path):
            reads[path] += 1
            return original(path)
        with patch.object(Path, "read_bytes", observe):
            result = self.reader.load_batch(self.root, [str(external), str(external)], auto=True)
        self.assertEqual(reads, Counter({external: 1}))
        self.assertEqual(result["sections"][0]["text"], "# Small external\n")

    def test_auto_invalid_later_source_produces_no_partial_stdout(self):
        external = self.external_file("# Valid external first\n")
        (self.root / "clients/codex.md").write_text("# Changed prepared source\n")
        stdout, stderr = StringIO(), StringIO()
        with patch.object(self.reader, "ROOT", self.root), \
                patch.object(sys, "argv", [str(SCRIPT), "--auto", "--json", "--batch", str(external),
                                          str(self.root / "clients/codex.md")]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.reader.main()
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Prepared source changed", stderr.getvalue())

    def test_json_cli_preserves_unicode_and_exclusive_helper_envelope(self):
        path = self.external_file("# Skill\r\nPreserve 😀, 漢字 and Straße.\r\n")
        command = [sys.executable, "-I", "-B", str(SCRIPT)]
        result = subprocess.run(command + ["--json", "--external", "--batch", str(path)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(packet["characters"], len(packet["text"]))
        self.assertIn(path.read_bytes().decode(), packet["text"])
        self.assertEqual([packet[k] for k in ("version", "kind", "page", "last_page", "pages", "external", "batch")],
                         [1, "instructions", 1, 1, 1, True, True])
        helper = subprocess.run(command + ["--helper"], capture_output=True, text=True, timeout=5)
        self.assertEqual(helper.returncode, 0, helper.stderr)
        data = json.loads(helper.stdout)
        self.assertEqual(data["kind"], "helper")
        self.assertEqual(data["characters"], len(data["text"]))
        self.assertEqual(data["text"], (PACKAGE / "scripts/drain_instructions.js").read_text())
        for flags in (["--prepare", "--json"], ["--helper", str(path)], ["--helper", "--external"],
                      ["--helper", "--json"], ["--helper", "--page", "2"], ["--helper", "--batch"]):
            rejected = subprocess.run(command + flags, capture_output=True, timeout=5)
            self.assertNotEqual(rejected.returncode, 0, flags)
            self.assertEqual(rejected.stdout, b"")

    def test_external_cli_delivers_selected_source_without_preparation_or_source_changes(self):
        path = self.external_file("# External skill\r\nPreserve Straße and 漢字.\r\n")
        original = path.read_bytes()
        manifest = (PACKAGE / "clients/instruction-map.json").read_bytes()
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--batch", "--external", str(path)],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(original, result.stdout)
        self.assertTrue(result.stdout.startswith(b"External instructions: pages 1-1/1;"))
        self.assertTrue(result.stdout.endswith(b"Selection complete.\n"))
        self.assertLessEqual(len(result.stdout), 24000)
        self.assertEqual(path.read_bytes(), original)
        invalid = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--prepare", "--external"],
                                 capture_output=True, timeout=5)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(invalid.stdout, b"")
        self.assertEqual((PACKAGE / "clients/instruction-map.json").read_bytes(), manifest)

    def test_external_batches_reconstruct_exact_sources_and_single_page_fallback(self):
        content = "# Skill\r\n" + "Preserve Straße, 漢字 and every rule.\r\n" * 1400
        path = self.external_file(content)
        other = self.external_file("# Reference\nDo not omit this last rule.\n")
        paths = [str(path), str(path), str(other)]
        first = self.reader.load_batch(self.root, paths, external=True)
        recovered = {str(path): "", str(other): ""}
        batch, count = first, 0
        while True:
            rendered = self.reader.render_page(batch)
            self.assertLessEqual(len(rendered.encode()), 24000)
            for section in batch["sections"]:
                recovered[section["path"]] += section["text"]
            count += 1
            page = batch["last_page"] + 1
            if page > batch["pages"]:
                break
            self.assertIn(f"--external --batch --page {page} --revision {first['revision']}", rendered)
            batch = self.reader.load_batch(self.root, paths, page, first["revision"], external=True)
        self.assertGreater(count, 1)
        self.assertLess(count, first["pages"])
        self.assertEqual(recovered, {str(path): content, str(other): other.read_text()})
        fallback = self.reader.load_page(self.root, paths, first["last_page"] + 1, first["revision"], external=True)
        self.assertLessEqual(len(self.reader.render_page(fallback).encode()), 9500)
        part = fallback["sections"][0]
        self.assertEqual(part["path"], str(path))
        self.assertEqual(part["start_line"], first["sections"][-1]["end_line"] + 1)
        self.assertEqual(part["text"], "".join(content.splitlines(keepends=True)[part["start_line"] - 1:part["end_line"]]))

    def test_external_read_observes_only_selected_source_once_without_a_manifest(self):
        path = self.external_file("# Small skill\nRead this complete requirement.\n")
        (self.root / "clients/instruction-map.json").unlink()
        reads = Counter()
        original_read = Path.read_bytes

        def observe(source):
            reads[source] += 1
            return original_read(source)

        with patch.object(Path, "read_bytes", observe):
            result = self.reader.load_batch(self.root, [str(path), str(path)], external=True)
        self.assertEqual((result["page"], result["last_page"], result["pages"]), (1, 1, 1))
        self.assertEqual(reads, Counter({path: 1}))
        self.assertEqual(result["sections"][0]["text"], "# Small skill\nRead this complete requirement.\n")

    def test_external_continuation_rejects_source_selection_and_resolved_target_changes(self):
        path = self.external_file(self.source)
        other = self.external_file(self.source)
        alias = path.parent / "alias.md"
        alias.symlink_to(path)
        paths = [str(alias)]
        first = self.reader.load_batch(self.root, paths, external=True)
        page = first["last_page"] + 1
        self.assertLessEqual(page, first["pages"])
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, page, external=True)
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths + [str(other)], page, first["revision"], external=True)
        path.write_text(self.source + "Changed required text.\n")
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, page, first["revision"], external=True)
        path.write_text(self.source)
        alias.unlink()
        alias.symlink_to(other)
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, page, first["revision"], external=True)

    def test_external_mode_cannot_bypass_package_or_markdown_path_boundaries(self):
        private = self.external_file("Private non-instruction data", name="private.txt")
        alias = private.parent / "alias.md"
        alias.symlink_to(private)
        package_alias = private.parent / "package.md"
        package_alias.symlink_to(self.root / "clients/codex.md")
        for path in ("relative.md", "https://example.test/skill.md", str(private), str(alias), str(package_alias),
                     str(self.root / "clients/codex.md"), str(private.parent), str(private.parent / "bad\nname.md")):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.reader.load_batch(self.root, [path], external=True)
        external = self.external_file("# Skill\n")
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, [str(external)])

    def test_external_cli_fails_without_partial_output_for_invalid_later_source(self):
        first = self.external_file("# Valid first source\n")
        invalid = self.external_file("漢" * 8000)
        for last in (invalid, invalid.parent / "missing.md"):
            stdout, stderr = StringIO(), StringIO()
            with patch.object(self.reader, "ROOT", self.root), \
                    patch.object(sys, "argv", [str(SCRIPT), "--external", "--batch", str(first), str(last)]), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                result = self.reader.main()
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("unavailable", stderr.getvalue())

    def test_pages_reconstruct_sources_once_with_complete_utf8_and_bounded_output(self):
        paths = ["clients/codex.md", "policies/rules.md", "policies/rules.md"]
        first = self.reader.load_page(self.root, paths)
        recovered = {path: "" for path in paths}
        for number in range(1, first["pages"] + 1):
            page = self.reader.load_page(self.root, paths, number, first["revision"] if number > 1 else None)
            self.assertLessEqual(len(self.reader.render_page(page).encode()), 9500)
            for section in page["sections"]:
                recovered[section["path"]] += section["text"]
            self.assertNotIn("unused procedure", self.reader.render_page(page))
        self.assertGreater(first["pages"], 1)
        self.assertEqual(recovered["policies/rules.md"], self.source)
        self.assertEqual(recovered["clients/codex.md"], "# Client\nRead applicable contracts.\n")

    def test_source_changes_reject_stale_map_and_prepared_continuation(self):
        paths = ["policies/rules.md"]
        first = self.reader.load_page(self.root, paths)
        (self.root / paths[0]).write_text(self.source + "New mandatory rule.\n")
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, paths)
        self.prepare()
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, paths, 2, first["revision"])
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, paths, 2)

    def test_invalid_selection_and_external_symlink_do_not_read_other_files(self):
        for paths in [[], ["../secret.md"], ["policies/missing.md"], [str(self.root.parent / "secret.md")]]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.reader.load_page(self.root, paths)
        outside = self.root.parent / (self.root.name + "-secret.md")
        outside.write_text("Private external text")
        self.addCleanup(outside.unlink)
        (self.root / "policies/rules.md").unlink()
        (self.root / "policies/rules.md").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, ["policies/rules.md"])

    def test_incomplete_and_overlapping_prepared_ranges_fail_before_delivery(self):
        path = self.root / "clients/instruction-map.json"
        original = json.loads(path.read_text())
        for ranges in [[[1, 2]], [[1, 4], [4, 901]], [[2, 901]], [[1, 9999]]]:
            manifest = json.loads(json.dumps(original))
            manifest["files"]["policies/rules.md"]["ranges"] = ranges
            path.write_text(json.dumps(manifest))
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                self.reader.load_page(self.root, ["policies/rules.md"])

    def test_unselected_changed_file_does_not_trigger_read_or_reprepare(self):
        (self.root / "policies/unrelated.md").unlink()
        page = self.reader.load_page(self.root, ["clients/codex.md"])
        self.assertEqual(len(page["sections"]), 1)
        self.assertEqual(page["sections"][0]["text"], "# Client\nRead applicable contracts.\n")

    def test_root_templates_are_prepared_instructions_but_other_root_folders_are_not(self):
        (self.root / "templates/nested").mkdir(parents=True)
        (self.root / "templates/brief.md").write_text("# Brief\n- Objective: [outcome]\n")
        (self.root / "templates/nested/deep.md").write_text("# Deep\n")
        (self.root / "assets").mkdir()
        (self.root / "assets/notes.md").write_text("# Notes\n")
        self.prepare()
        files = json.loads((self.root / "clients/instruction-map.json").read_text())["files"]
        self.assertIn("templates/brief.md", files)
        self.assertNotIn("templates/nested/deep.md", files)
        self.assertNotIn("assets/notes.md", files)
        page = self.reader.load_page(self.root, ["templates/brief.md"])
        self.assertEqual(page["sections"][0]["text"], "# Brief\n- Objective: [outcome]\n")
        import hashlib
        map_path = self.root / "clients/instruction-map.json"
        manifest = json.loads(map_path.read_text())
        for name in ("templates/nested/deep.md", "assets/notes.md"):
            # A forged entry must not get past the folder rule.
            manifest["files"][name] = dict(sha256=hashlib.sha256((self.root / name).read_bytes()).hexdigest(), ranges=[[1, 1]])
        map_path.write_text(json.dumps(manifest))
        for name in ("templates/nested/deep.md", "assets/notes.md"):
            with self.assertRaises(ValueError):
                self.reader.load_page(self.root, [name])

    def test_preparation_rejects_unsplittable_line_instead_of_clipping_it(self):
        (self.root / "policies/rules.md").write_text("漢" * 8000)
        with self.assertRaises(ValueError):
            self.reader.build_manifest(self.root)

    def test_forged_map_cannot_select_non_instruction_package_files(self):
        import hashlib
        private = self.root / "private.txt"
        private.write_text("Private package data\n")
        path = self.root / "clients/instruction-map.json"
        manifest = json.loads(path.read_text())
        manifest["files"]["private.txt"] = dict(sha256=hashlib.sha256(private.read_bytes()).hexdigest(), ranges=[[1, 1]])
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, ["private.txt"])

    def test_internal_symlink_cannot_reclassify_non_instruction_package_data(self):
        private = self.root / "private.txt"
        private.write_text("Private package data\n")
        alias = self.root / "policies/alias.md"
        alias.symlink_to("../private.txt")
        with self.assertRaises(ValueError):
            self.reader.build_manifest(self.root)
        alias.unlink()
        original = self.root / "policies/rules.md"
        original.unlink()
        original.symlink_to("../private.txt")
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, ["policies/rules.md"])

    def test_malformed_map_has_a_controlled_failure(self):
        for value in [[], None, {"version": 1, "files": []}]:
            (self.root / "clients/instruction-map.json").write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.reader.load_page(self.root, ["clients/codex.md"])

    def test_real_bootstrap_cli_is_bounded_and_package_manifest_is_current(self):
        self.assertEqual(json.loads((PACKAGE / "clients/instruction-map.json").read_text()),
                         self.reader.build_manifest(PACKAGE))
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "bootstrap"],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout), 9500)
        self.assertIn(b"clients/codex.md", result.stdout)
        self.assertNotIn(b"runtime.sqlite3", result.stdout)

    def test_batch_cli_retains_complete_bootstrap_and_rejects_preparation(self):
        before = (PACKAGE / "clients/instruction-map.json").read_bytes()
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--batch", "bootstrap"],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"Selection complete.", result.stdout)
        self.assertLessEqual(len(result.stdout), 24000)
        for name in ("clients/codex.md", "policies/00-index.md"):
            self.assertIn((PACKAGE / name).read_bytes(), result.stdout)
        invalid = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--prepare", "--batch"],
                                 capture_output=True, timeout=5)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(invalid.stdout, b"")
        self.assertEqual((PACKAGE / "clients/instruction-map.json").read_bytes(), before)

    def test_batches_reconstruct_exact_utf8_sources_with_fewer_responses(self):
        self.assertTrue(hasattr(self.reader, "load_batch"), "Bounded batch reader is missing")
        paths = ["clients/codex.md", "policies/rules.md", "policies/rules.md"]
        first = self.reader.load_batch(self.root, paths)
        self.assertGreater(first["last_page"], first["page"])
        recovered = {name: "" for name in paths}
        page, count = 1, 0
        while page <= first["pages"]:
            batch = self.reader.load_batch(self.root, paths, page, first["revision"] if page > 1 else None)
            self.assertGreaterEqual(batch["last_page"], page)
            text = self.reader.render_page(batch)
            self.assertLessEqual(len(text.encode()), 24000)
            self.assertNotIn("unused procedure", text)
            for section in batch["sections"]:
                recovered[section["path"]] += section["text"]
            if batch["last_page"] < batch["pages"]:
                self.assertIn(f"--batch --page {batch['last_page'] + 1} --revision {first['revision']}", text)
            else:
                self.assertIn("Selection complete.", text)
            count += 1
            page = batch["last_page"] + 1
        self.assertLess(count, first["pages"])
        self.assertEqual(recovered["policies/rules.md"], self.source)
        self.assertEqual(recovered["clients/codex.md"], "# Client\nRead applicable contracts.\n")

    def test_batch_observes_each_selected_source_once(self):
        self.assertTrue(hasattr(self.reader, "load_batch"), "Bounded batch reader is missing")
        reads = Counter()
        read_bytes = Path.read_bytes

        def observe(path):
            reads[path] += 1
            return read_bytes(path)

        with patch.object(Path, "read_bytes", observe):
            batch = self.reader.load_batch(self.root, ["clients/codex.md", "policies/rules.md"])
        self.assertGreater(batch["last_page"], 1)
        self.assertEqual(reads, Counter({(self.root / name).resolve(): 1 for name in
                                        ("clients/instruction-map.json", "clients/codex.md", "policies/rules.md")}))

    def test_batch_includes_metadata_in_bound_and_single_page_fallback_resumes_exactly(self):
        self.assertTrue(hasattr(self.reader, "load_batch"), "Bounded batch reader is missing")
        paths = ["policies/" + letter * 120 + ".md" for letter in "abc"]
        for name in paths:
            (self.root / name).write_text("x" * 7899 + "\n")
        self.prepare()
        first = self.reader.load_batch(self.root, paths)
        self.assertEqual(first["pages"], 3)
        self.assertEqual(first["last_page"], 2)
        self.assertLessEqual(len(self.reader.render_page(first).encode()), 24000)
        final = self.reader.load_page(self.root, paths, 3, first["revision"])
        self.assertEqual([s["path"] for s in final["sections"]], [paths[2]])
        self.assertEqual(final["sections"][0]["text"], "x" * 7899 + "\n")
        self.assertLessEqual(len(self.reader.render_page(final).encode()), 9500)

    def test_batch_continuation_rejects_missing_stale_or_changed_selection(self):
        self.assertTrue(hasattr(self.reader, "load_batch"), "Bounded batch reader is missing")
        paths = ["policies/rules.md"]
        first = self.reader.load_batch(self.root, paths)
        next_page = first["last_page"] + 1
        self.assertLessEqual(next_page, first["pages"])
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, next_page)
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths + ["clients/codex.md"], next_page, first["revision"])
        (self.root / paths[0]).write_text(self.source + "New mandatory instruction.\n")
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, next_page, first["revision"])
        self.prepare()
        with self.assertRaises(ValueError):
            self.reader.load_batch(self.root, paths, next_page, first["revision"])

    def test_batch_cli_rejects_later_invalid_source_without_partial_stdout(self):
        (self.root / "policies/rules.md").write_text("Changed mandatory instructions\n")
        stdout, stderr = StringIO(), StringIO()
        with patch.object(self.reader, "ROOT", self.root), \
                patch.object(sys, "argv", [str(SCRIPT), "--batch", "clients/codex.md", "policies/rules.md"]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.reader.main()
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Prepared source changed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
