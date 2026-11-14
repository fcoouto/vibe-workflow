"""Portable artifact layout and rejection checks; no dependency or native operations."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.package_opencode import prepare


class OpenCodePackageTests(unittest.TestCase):
    def test_preserves_clean_export_helpers_and_adds_only_scoped_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = str(Path(temp).resolve())
            root = Path(temp) / "source"
            adapter = root / "runtime/adapters/opencode_workflow"
            adapter.mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "scripts/workflow.py").write_text("print('helper')\n")
            (adapter / "index.ts").write_text("export default {}\n")
            (adapter / "server.ts").write_text("export default {}\n")
            (adapter / "package.json").write_text(json.dumps({"name": "test", "exports": {".": "./server.ts",
                "./gui-experimental": "./dist/gui-experimental.cjs"}, "scripts": {"build": "vite build"}}))
            output = Path(temp) / "artifact"
            with patch("scripts.package.tracked_files", return_value=["scripts/workflow.py", "runtime/adapters/opencode_workflow/index.ts"]):
                report = prepare(root, output)
            self.assertEqual(report["client_execution"], "not_checked")
            package = json.loads((output / "package.json").read_text())
            self.assertTrue((output / package["exports"]["."]).is_file())
            self.assertTrue((output / "runtime/adapters/opencode_workflow/../../../scripts/workflow.py").is_file())
            self.assertFalse((output / "node_modules").exists())

    def test_foreign_existing_or_checkout_outputs_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = str(Path(temp).resolve())
            root = Path(temp) / "source"
            root.mkdir()
            foreign = Path(temp) / "foreign"
            foreign.mkdir()
            (foreign / "keep").write_text("original")
            for output in [foreign, root / "inside", root.parent]:
                with self.assertRaises(ValueError):
                    prepare(root, output)
            self.assertEqual((foreign / "keep").read_text(), "original")

    def test_parent_traversal_cannot_place_output_inside_source(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            root = parent / "source"
            root.mkdir()
            sibling = parent / "sibling"
            sibling.mkdir()
            adapter = root / "runtime/adapters/opencode_workflow"
            adapter.mkdir(parents=True)
            (adapter / "package.json").write_text(json.dumps({"name": "test", "exports": {}}))
            with patch("scripts.package.tracked_files", return_value=[]):
                with self.assertRaises(ValueError):
                    prepare(root, sibling / ".." / "source" / "inside")
            self.assertFalse((root / "inside").exists())

    def test_symlink_parent_cannot_redirect_output_to_foreign_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            root = parent / "source"
            root.mkdir()
            foreign = parent / "foreign"
            foreign.mkdir()
            alias = parent / "alias"
            alias.symlink_to(foreign, target_is_directory=True)
            with self.assertRaises(ValueError):
                prepare(root, alias / "artifact")
            self.assertFalse((foreign / "artifact").exists())
