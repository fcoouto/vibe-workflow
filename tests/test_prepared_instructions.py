"""Instruction delivery must read the selected current bytes and preserve exact instructions, scope and revisions."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts.validate import validate
from tests.fixture_package import copy_package


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/read_instructions.py"
MAP = "clients/instruction-map.json"


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
        # No instruction map exists: the reader derives hashes and ranges from the selected current bytes.
        self.assertFalse((self.root / MAP).exists())

    def external_file(self, text, name="Ü skill.md"):
        directory = tempfile.TemporaryDirectory(prefix="external instructions ")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name).resolve() / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def observed_reads(self, action):
        reads = Counter()
        original = Path.read_bytes

        def observe(path):
            reads[path] += 1
            return original(path)

        with patch.object(Path, "read_bytes", observe):
            result = action()
        return result, reads

    def run_main(self, *arguments):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(self.reader, "ROOT", self.root), \
                patch.object(sys, "argv", [str(SCRIPT), *arguments]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.reader.main()
        return result, stdout.getvalue(), stderr.getvalue()

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

    def test_auto_package_aliases_keep_package_authority_and_read_current_bytes(self):
        # Detects: any remaining dependence on an instruction map for alias authority or freshness.
        alias = self.root / "policies/alias.md"
        alias.symlink_to("rules.md")
        outside = self.external_file("# Outside\n")
        directory_alias = outside.parent / "package"
        directory_alias.symlink_to(self.root, target_is_directory=True)
        outside_alias = outside.parent / "package-source.md"
        outside_alias.symlink_to(self.root / "policies/rules.md")
        for name in (str(alias), str(directory_alias / "policies/alias.md"), str(outside_alias)):
            self.assertIn("Preserve Straße", self.reader.render_page(self.reader.load_page(self.root, [name], auto=True)))
        (self.root / "policies/rules.md").write_text("# Changed package source\n")
        for name in ("policies/alias.md", str(alias), str(directory_alias / "policies/alias.md"), str(outside_alias)):
            with self.subTest(name=name):
                page = self.reader.load_page(self.root, [name], auto=True)
                self.assertEqual(page["sections"][0]["text"], "# Changed package source\n")
                self.assertEqual(page["sections"][0]["path"], name)

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
        result, reads = self.observed_reads(
            lambda: self.reader.load_batch(self.root, [str(external), str(external)], auto=True))
        self.assertEqual(reads, Counter({external: 1}))
        self.assertEqual(result["sections"][0]["text"], "# Small external\n")

    def test_auto_cli_fails_without_partial_stdout_for_genuinely_invalid_later_sources(self):
        # Detects: output printed before the whole selection is validated, and a missing, non-UTF-8,
        # overlong or forbidden later source being skipped instead of refusing the read.
        external = self.external_file("# Valid external first\n")
        later = self.root / "clients/later.md"
        forbidden = self.root / "assets"
        forbidden.mkdir()
        (forbidden / "notes.md").write_text("# Not an instruction source\n")
        cases = {
            "invalid UTF-8": lambda: later.write_bytes(b"# Bad \xff\xfe bytes\n"),
            "overlong line": lambda: later.write_text("漢" * 8000),
            "missing": lambda: later.unlink(missing_ok=True),
            "forbidden class": lambda: later.write_text("# placeholder\n"),
        }
        for label, prepare in cases.items():
            prepare()
            target = forbidden / "notes.md" if label == "forbidden class" else later
            with self.subTest(case=label):
                result, stdout, stderr = self.run_main("--auto", "--json", "--batch", str(external), str(target))
                self.assertEqual(result, 1)
                self.assertEqual(stdout, "")
                self.assertIn("unavailable", stderr)

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

    def test_external_cli_delivers_selected_source_without_source_changes(self):
        path = self.external_file("# External skill\r\nPreserve Straße and 漢字.\r\n")
        original = path.read_bytes()
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
        result, reads = self.observed_reads(
            lambda: self.reader.load_batch(self.root, [str(path), str(path)], external=True))
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
            result, stdout, stderr = self.run_main("--external", "--batch", str(first), str(last))
            self.assertEqual(result, 1)
            self.assertEqual(stdout, "")
            self.assertIn("unavailable", stderr)

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

    def test_new_and_edited_package_sources_load_immediately_without_any_preparation(self):
        # Detects: a stale or missing prepared map gating first reads of existing, new or edited sources.
        (self.root / "skills/demo/references").mkdir(parents=True)
        (self.root / "templates/nested").mkdir(parents=True)
        created = {"policies/new.md": "# New policy\nNew mandatory rule.\n",
                   "skills/demo/references/new.md": "# New skill reference\r\nKeep 😀 exactly.\r\n",
                   "templates/nested/new.md": "# New template\n"}
        for name, text in created.items():
            (self.root / name).write_bytes(text.encode())
        (self.root / "policies/rules.md").write_text(self.source + "Edited mandatory rule.\n")
        for name, text in created.items():
            with self.subTest(name=name):
                self.assertEqual(self.reader.load_page(self.root, [name])["sections"][0]["text"], text)
        first = self.reader.load_page(self.root, ["policies/rules.md"])
        last = self.reader.load_page(self.root, ["policies/rules.md"], first["pages"],
                                     first["revision"] if first["pages"] > 1 else None)
        self.assertTrue(last["sections"][-1]["text"].endswith("Edited mandatory rule.\n"))

    def test_source_edit_is_readable_at_once_while_an_old_continuation_revision_fails(self):
        # Detects: reintroduced first-read stale-map rejection, or a continuation accepted across a source edit.
        paths = ["policies/rules.md"]
        first = self.reader.load_page(self.root, paths)
        self.assertGreater(first["pages"], 1)
        (self.root / paths[0]).write_text(self.source + "New mandatory rule.\n")
        fresh = self.reader.load_page(self.root, paths)
        self.assertNotEqual(fresh["revision"], first["revision"])
        self.assertTrue(self.reader.load_page(self.root, paths, fresh["pages"], fresh["revision"])["sections"][-1]["text"]
                        .endswith("New mandatory rule.\n"))
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, paths, 2, first["revision"])
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, paths, 2)

    def test_package_continuation_revisions_bind_selection_mode_authority_and_alias_target(self):
        same = self.root / "policies/same.md"
        same.write_text(self.source)
        alias = self.root / "policies/alias.md"
        alias.symlink_to("rules.md")
        paths = ["clients/codex.md", "policies/rules.md"]
        first = self.reader.load_page(self.root, paths)
        self.assertGreater(first["pages"], 1)
        self.assertEqual(self.reader.load_page(self.root, paths, 2, first["revision"])["revision"], first["revision"])
        for label, selection, options in (
                ("reordered", paths[::-1], {}), ("added", paths + ["policies/same.md"], {}),
                ("removed", paths[1:], {}), ("automatic mode", paths, {"auto": True})):
            with self.subTest(change=label), self.assertRaises(ValueError):
                self.reader.load_page(self.root, selection, 2, first["revision"], **options)
        automatic = self.reader.load_page(self.root, [str(alias)], auto=True)
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, [str(alias)], 2, automatic["revision"], external=True)
        alias.unlink()
        alias.symlink_to("same.md")  # Same bytes, different resolved target.
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, [str(alias)], 2, automatic["revision"], auto=True)
        self.assertEqual(self.reader.load_page(self.root, [str(alias)], auto=True)["page"], 1)

    def test_revisions_keep_their_serialization_and_the_prepared_key_field(self):
        # Detects: a renamed or dropped revision field after the map is removed.
        raw = (self.root / "clients/codex.md").read_bytes()
        entry = self.reader.source_entry(raw, "clients/codex.md")
        package = self.reader.load_page(self.root, ["clients/codex.md"])
        self.assertEqual(package["revision"], hashlib.sha256(
            json.dumps([["clients/codex.md", entry]], sort_keys=True).encode()).hexdigest())
        automatic = self.reader.load_page(self.root, ["clients/codex.md"], auto=True)
        identity = dict(mode="auto", sources=[["clients/codex.md", dict(
            entry, authority="package", resolved=str((self.root / "clients/codex.md").resolve()),
            prepared_key="clients/codex.md")]])
        self.assertEqual(automatic["revision"], hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest())
        self.assertNotEqual(automatic["revision"], package["revision"])

    def test_invalid_selection_and_external_symlink_do_not_read_other_files(self):
        for paths in [[], ["../secret.md"], [str(self.root.parent / "secret.md")]]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.reader.load_page(self.root, paths)
        with self.assertRaises((ValueError, OSError)):
            self.reader.load_page(self.root, ["policies/missing.md"])
        outside = self.root.parent / (self.root.name + "-secret.md")
        outside.write_text("Private external text")
        self.addCleanup(outside.unlink)
        (self.root / "policies/rules.md").unlink()
        (self.root / "policies/rules.md").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, ["policies/rules.md"])

    def test_ranges_are_derived_from_current_bytes_and_reconstruct_exactly_at_size_boundaries(self):
        # Detects: ranges taken from stored data, gaps or overlaps, and clipping at the 8,000-byte source budget.
        cases = {"policies/lines.md": "".join("y" * 3999 + "\n" for _ in range(5)),
                 "policies/exact.md": "x" * 7999 + "\n" + "z\n",
                 "policies/cjk.md": "漢" * 2666 + "\n" + "漢" * 2666 + "\r\n" + "é\n",
                 "policies/nonewline.md": "漢" * 2666 + "ab"}
        for name, text in cases.items():
            (self.root / name).write_bytes(text.encode())
            with self.subTest(name=name):
                lines = text.encode().decode().splitlines(keepends=True)
                entry = self.reader.source_entry(text.encode(), name)
                self.assertEqual(entry["sha256"], hashlib.sha256(text.encode()).hexdigest())
                start = 1
                for span in entry["ranges"]:
                    self.assertEqual(span[0], start)
                    self.assertLessEqual(len("".join(lines[span[0] - 1:span[1]]).encode()), 8000)
                    start = span[1] + 1
                self.assertEqual(start, len(lines) + 1)
                pages = self.reader.load_page(self.root, [name])["pages"]
                recovered = ""
                for number in range(1, pages + 1):
                    page = self.reader.load_page(self.root, [name], number, None if number == 1 else
                                                 self.reader.load_page(self.root, [name])["revision"])
                    recovered += "".join(section["text"] for section in page["sections"])
                self.assertEqual(recovered, text)
        for name, text in {"policies/long.md": "x" * 8001 + "\n", "policies/wide.md": "漢" * 2667}.items():
            (self.root / name).write_bytes(text.encode())
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "exceeds the section budget"):
                self.reader.load_page(self.root, [name])

    def test_unselected_changed_missing_or_invalid_sources_do_not_affect_or_get_read(self):
        # Detects: any enumeration or preparation pass over the whole package during a selected read.
        (self.root / "policies/unrelated.md").write_bytes(b"\xff\xfe invalid unselected source")
        (self.root / "policies/overlong.md").write_text("漢" * 8000)
        (self.root / "policies/rules.md").unlink()
        page, reads = self.observed_reads(lambda: self.reader.load_page(self.root, ["clients/codex.md"]))
        self.assertEqual(len(page["sections"]), 1)
        self.assertEqual(page["sections"][0]["text"], "# Client\nRead applicable contracts.\n")
        self.assertEqual(reads, Counter({(self.root / "clients/codex.md").resolve(): 1}))

    def test_nested_templates_and_skills_are_sources_but_other_files_are_not(self):
        (self.root / "templates/nested").mkdir(parents=True)
        (self.root / "skills/demo/references").mkdir(parents=True)
        (self.root / "clients/nested").mkdir()
        (self.root / "policies/nested").mkdir()
        (self.root / "templates/brief.md").write_text("# Brief\n- Objective: [outcome]\n")
        (self.root / "templates/nested/deep.md").write_text("# Deep\n")
        (self.root / "skills/demo/references/deep.md").write_text("# Deep skill\n")
        (self.root / "templates/nested/private.txt").write_text("Private data\n")
        (self.root / "clients/nested/hidden.md").write_text("# Nested client\n")
        (self.root / "policies/nested/hidden.md").write_text("# Nested policy\n")
        (self.root / "private.txt").write_text("Private package text\n")
        (self.root / "assets").mkdir()
        (self.root / "assets/notes.md").write_text("# Notes\n")
        for name, text in (("templates/brief.md", "# Brief\n- Objective: [outcome]\n"),
                           ("templates/nested/deep.md", "# Deep\n"), ("skills/demo/references/deep.md", "# Deep skill\n")):
            self.assertEqual(self.reader.load_page(self.root, [name])["sections"][0]["text"], text)
        for name in ("templates/nested/private.txt", "assets/notes.md", "templates/../assets/notes.md", "private.txt",
                     "clients/nested/hidden.md", "policies/nested/hidden.md", "/etc/hosts", "skills/SKILL.md"):
            with self.subTest(name=name):
                with self.assertRaises((ValueError, OSError)):
                    self.reader.load_page(self.root, [name])
        # Direct classification refusal of existing files, independent of any selection list.
        for name in ("templates/nested/private.txt", "assets/notes.md", "private.txt"):
            with self.subTest(source_path=name), self.assertRaises(ValueError):
                self.reader.source_path(self.root, name)

    def test_nested_template_symlinks_cannot_escape_or_reclassify_package_data(self):
        (self.root / "templates/project").mkdir(parents=True)
        external = self.external_file("Private external text\n")
        alias = self.root / "templates/project/alias.md"
        alias.symlink_to(external)
        for name in ("templates/project/alias.md", str(alias)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.reader.load_page(self.root, [name], auto=name != "templates/project/alias.md")
        with self.assertRaises(ValueError):
            self.reader.source_path(self.root, "templates/project/alias.md")
        alias.unlink()
        (self.root / "private.txt").write_text("Private package text\n")
        alias.symlink_to("../../private.txt")
        with self.assertRaisesRegex(ValueError, "packaged instruction file"):
            self.reader.load_page(self.root, ["templates/project/alias.md"])

    def test_unsplittable_line_is_refused_at_read_instead_of_clipped(self):
        (self.root / "policies/rules.md").write_text("漢" * 8000)
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, ["policies/rules.md"])
        result, stdout, stderr = self.run_main("--batch", "policies/rules.md")
        self.assertEqual((result, stdout), (1, ""))

    def test_instruction_map_has_no_source_exemption_and_is_ignored_when_present(self):
        # Detects: the map exemption in source_path and any parsing of a present, stale, forged or malformed map.
        map_path = self.root / MAP
        map_path.write_text("{}")
        with self.assertRaises(ValueError):
            self.reader.source_path(self.root, MAP)
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, [MAP])
        for value in ([], None, {"version": 1, "files": []},
                      {"version": 1, "files": {"clients/codex.md": {"sha256": "0" * 64, "ranges": [[1, 1]]}}}):
            map_path.write_text(json.dumps(value))
            with self.subTest(value=value):
                page = self.reader.load_page(self.root, ["clients/codex.md"])
                self.assertEqual(page["sections"][0]["text"], "# Client\nRead applicable contracts.\n")
                self.assertEqual((page["sections"][0]["start_line"], page["sections"][0]["end_line"]), (1, 2))

    def test_forged_selection_cannot_reach_non_instruction_package_files(self):
        private = self.root / "private.txt"
        private.write_text("Private package data\n")
        for name in ("private.txt", "clients/../private.txt", str(private)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.reader.load_page(self.root, [name])

    def test_internal_symlink_cannot_reclassify_non_instruction_package_data(self):
        private = self.root / "private.txt"
        private.write_text("Private package data\n")
        alias = self.root / "policies/alias.md"
        alias.symlink_to("../private.txt")
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, ["policies/alias.md"])
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, [str(alias)], auto=True)
        alias.unlink()
        original = self.root / "policies/rules.md"
        original.unlink()
        original.symlink_to("../private.txt")
        with self.assertRaises(ValueError):
            self.reader.load_page(self.root, ["policies/rules.md"])

    def test_removed_prepare_option_is_rejected_and_writes_nothing(self):
        # Detects: a surviving --prepare writer or a success-shaped no-op. Runs a copy of the script in a
        # disposable package root, never the real checkout.
        (self.root / "scripts").mkdir()
        copy = self.root / "scripts/read_instructions.py"
        shutil.copyfile(SCRIPT, copy)
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        for flags in (["--prepare"], ["--prepare", "--json"], ["--prepare", "--batch"]):
            with self.subTest(flags=flags):
                result = subprocess.run([sys.executable, "-I", "-B", str(copy)] + flags, capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
        self.assertEqual(sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*")), before)
        self.assertFalse((self.root / MAP).exists())
        self.assertFalse(hasattr(self.reader, "build_manifest"))
        self.assertFalse(hasattr(self.reader, "MAP"))

    def test_package_has_no_instruction_map_and_every_real_source_reads_from_current_bytes(self):
        self.assertFalse((PACKAGE / MAP).exists(), "The instruction map must be deleted")
        sources = sorted([*(PACKAGE / "clients").glob("*.md"), *(PACKAGE / "policies").glob("*.md"),
                          *(PACKAGE / "templates").rglob("*.md"), *(PACKAGE / "skills").rglob("*.md")])
        self.assertGreater(len(sources), 50)
        for path in sources:
            name = path.relative_to(PACKAGE).as_posix()
            with self.subTest(name=name):
                entry = self.reader.source_entry(self.reader.source_path(PACKAGE, name).read_bytes(), name)
                self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                first = self.reader.load_page(PACKAGE, [name])
                recovered = ""
                for number in range(1, first["pages"] + 1):
                    page = self.reader.load_page(PACKAGE, [name], number, first["revision"] if number > 1 else None)
                    recovered += "".join(section["text"] for section in page["sections"])
                self.assertEqual(recovered, path.read_bytes().decode("utf-8"))

    def test_real_bootstrap_cli_is_bounded_without_a_package_manifest(self):
        self.assertFalse((PACKAGE / MAP).exists())
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "bootstrap"],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout), 9500)
        self.assertIn(b"clients/codex.md", result.stdout)
        self.assertNotIn(b"runtime.sqlite3", result.stdout)

    def test_batch_cli_retains_complete_bootstrap_and_rejects_preparation(self):
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
        self.assertFalse((PACKAGE / MAP).exists())

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

    def test_batch_observes_each_selected_source_once_and_no_map(self):
        self.assertTrue(hasattr(self.reader, "load_batch"), "Bounded batch reader is missing")
        batch, reads = self.observed_reads(
            lambda: self.reader.load_batch(self.root, ["clients/codex.md", "policies/rules.md", "clients/codex.md"]))
        self.assertGreater(batch["last_page"], 1)
        self.assertEqual(reads, Counter({(self.root / name).resolve(): 1 for name in
                                        ("clients/codex.md", "policies/rules.md")}))

    def test_batch_includes_metadata_in_bound_and_single_page_fallback_resumes_exactly(self):
        self.assertTrue(hasattr(self.reader, "load_batch"), "Bounded batch reader is missing")
        paths = ["policies/" + letter * 120 + ".md" for letter in "abc"]
        for name in paths:
            (self.root / name).write_text("x" * 7899 + "\n")
        first = self.reader.load_batch(self.root, paths)
        self.assertEqual(first["pages"], 3)
        self.assertEqual(first["last_page"], 2)
        self.assertLessEqual(len(self.reader.render_page(first).encode()), 24000)
        final = self.reader.load_page(self.root, paths, 3, first["revision"])
        self.assertEqual([s["path"] for s in final["sections"]], [paths[2]])
        self.assertEqual(final["sections"][0]["text"], "x" * 7899 + "\n")
        self.assertLessEqual(len(self.reader.render_page(final).encode()), 9500)

    def test_batch_continuation_rejects_missing_or_changed_revision_and_selection(self):
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
        fresh = self.reader.load_batch(self.root, paths)  # A fresh first batch reads the edited bytes immediately.
        self.assertNotEqual(fresh["revision"], first["revision"])

    def test_batch_cli_rejects_genuinely_invalid_later_sources_without_partial_stdout(self):
        # Detects: partial stdout, or a missing, non-UTF-8, overlong or forbidden later source being skipped.
        later = self.root / "policies/rules.md"
        forbidden = self.root / "assets"
        forbidden.mkdir()
        (forbidden / "notes.md").write_text("# Not an instruction source\n")
        for label, prepare, name in (
                ("invalid UTF-8", lambda: later.write_bytes(b"Changed \xff\xfe\n"), "policies/rules.md"),
                ("overlong line", lambda: later.write_text("漢" * 8000), "policies/rules.md"),
                ("missing", lambda: later.unlink(), "policies/rules.md"),
                ("forbidden class", lambda: None, "assets/notes.md")):
            prepare()
            with self.subTest(case=label):
                result, stdout, stderr = self.run_main("--batch", "clients/codex.md", name)
                self.assertEqual(result, 1)
                self.assertEqual(stdout, "")
                self.assertIn("unavailable", stderr)


class MaplessPackageValidationTests(unittest.TestCase):
    """Package validation reads every supported instruction source directly; no map is needed or written."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.package = Path(cls.temp.name) / "package"
        copy_package(PACKAGE, cls.package, ".superpowers")
        (cls.package / MAP).unlink(missing_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def assert_refused(self, name, writer, pattern=None):
        path = self.package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        writer(path)
        try:
            with (self.assertRaisesRegex(ValueError, pattern) if pattern else self.assertRaises((ValueError, OSError))):
                validate(self.package)
        finally:
            path.unlink(missing_ok=True)

    def test_valid_package_without_a_map_passes_and_validation_writes_none(self):
        # Detects: validation still requiring, comparing against or regenerating the instruction map.
        self.assertFalse((self.package / MAP).exists())
        self.assertEqual(validate(self.package)["validation"], "passed")
        self.assertFalse((self.package / MAP).exists())

    def test_bad_unselected_sources_in_every_supported_set_fail_validation(self):
        # Detects: validation skipping sources no caller selected, including nested skills and templates.
        self.assertEqual(validate(self.package)["validation"], "passed")
        bad_bytes = lambda path: path.write_bytes(b"# Invalid \xff\xfe bytes\n")
        overlong = lambda path: path.write_text("漢" * 8000)
        for name, writer, pattern in (
                ("clients/zz-unselected.md", bad_bytes, None),
                ("policies/zz-unselected.md", overlong, "exceeds the section budget"),
                ("templates/zz-nested/unselected.md", overlong, "exceeds the section budget"),
                ("templates/project/zz-unselected.md", bad_bytes, None),
                ("skills/zz-unselected/references/unselected.md", bad_bytes, None),
                ("skills/zz-unselected/SKILL.md", overlong, "exceeds the section budget")):
            with self.subTest(source=name):
                self.assert_refused(name, writer, pattern)
        self.assertEqual(validate(self.package)["validation"], "passed")

    def test_escaping_or_reclassified_sources_fail_validation_without_reading_outside_the_package(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("# Private external text\n")
        (self.package / "private.txt").write_text("Private package text\n")
        self.addCleanup((self.package / "private.txt").unlink)
        for name, target in (("templates/project/zz-escape.md", outside),
                             ("policies/zz-escape.md", outside),
                             ("skills/zz-escape/references/escape.md", outside),
                             ("policies/zz-reclassified.md", "../private.txt"),
                             ("templates/project/zz-reclassified.md", "../../private.txt")):
            with self.subTest(source=name):
                self.assert_refused(name, lambda path, target=target: path.symlink_to(target))

    def test_files_outside_the_four_supported_source_sets_are_not_instruction_sources(self):
        # Detects: a validator scan broader than clients/*.md, policies/*.md, templates/**/*.md, skills/**/*.md.
        for name in ("clients/nested/ignored.md", "policies/nested/ignored.md", "assets/zz-notes.md"):
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# Not scanned \xff\xfe\n")
            self.addCleanup(path.unlink, missing_ok=True)
        self.assertEqual(validate(self.package)["validation"], "passed")


if __name__ == "__main__":
    unittest.main()
