"""Exercise the read-only workspace check with injected probes, never the network."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/vibe-prepare-my-workspace/scripts/check.py"


def load():
    spec = importlib.util.spec_from_file_location("workspace_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.check = load()

    def test_parse_version_takes_the_numeric_prefix(self):
        parse = self.check.parse_version
        self.assertEqual(parse("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse("rtk 1.1.0"), (1, 1, 0))
        self.assertEqual(parse("git version 2.39.5 (Apple Git-154)"), (2, 39, 5))
        self.assertEqual(parse("1.0.0-rc1"), (1, 0, 0))
        with self.assertRaises(ValueError):
            parse("none")

    def test_compare_pads_and_orders_numerically(self):
        compare = self.check.compare
        self.assertEqual(compare("1.2", "1.2.0"), 0)
        self.assertEqual(compare("1.10.0", "1.9.9"), 1)
        self.assertEqual(compare("1.9.9", "1.10.0"), -1)

    def test_exit_code_prefers_action_over_unknown(self):
        code = self.check.exit_code
        self.assertEqual(code(["ready"]), 0)
        self.assertEqual(code(["ready", "unknown"]), 2)
        self.assertEqual(code(["outdated", "unknown"]), 1)
        self.assertEqual(code(["missing"]), 1)
        self.assertEqual(code(["not-supported", "ready"]), 0)

    def test_worst_state_precedence(self):
        self.assertEqual(self.check.worst(["ready", "unknown", "missing", "outdated"]), "missing")
        self.assertEqual(self.check.worst(["ready", "unknown"]), "unknown")


class LocalTest(unittest.TestCase):
    def setUp(self):
        self.check = load()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.config_dir = self.home / ".config/opencode"
        self.config_dir.mkdir(parents=True)

    def write_config(self, text):
        path = self.config_dir / "opencode.jsonc"
        path.write_text(text)
        return path

    def test_read_config_keeps_urls_and_drops_comments(self):
        path = self.write_config(
            '{\n  // line comment\n  "url": "https://x.test/a", /* block */\n'
            '  "list": ["a", "b",],\n}\n')
        self.assertEqual(self.check.read_config(path), {"url": "https://x.test/a", "list": ["a", "b"]})

    def test_read_config_rejects_garbage_and_missing_files(self):
        with self.assertRaises(ValueError):
            self.check.read_config(self.write_config("not json"))
        with self.assertRaises(ValueError):
            self.check.read_config(self.home / "absent.json")

    def test_plugin_matches_both_keys_and_option_pairs(self):
        present = self.check.integration_present
        config = {"plugin": ["legacy-tool"], "plugins": ["superpowers@git+https://x.test/s.git", ["ponytail", {"a": 1}]]}
        for match in ("legacy-tool", "superpowers", "ponytail"):
            self.assertTrue(present({"kind": "plugin", "match": match}, config, self.home)[0], match)
        self.assertFalse(present({"kind": "plugin", "match": "absent"}, config, self.home)[0])

    def test_mcp_file_and_skill_presence(self):
        present = self.check.integration_present
        self.assertTrue(present({"kind": "mcp", "name": "context7"}, {"mcp": {"servers": {"context7": {}}}}, self.home)[0])
        self.assertTrue(present({"kind": "mcp", "name": "context7"}, {"mcp": {"context7": {}}}, self.home)[0])
        self.assertFalse(present({"kind": "mcp", "name": "context7"}, {}, self.home)[0])
        (self.config_dir / "plugins").mkdir()
        (self.config_dir / "plugins/rtk.ts").write_text("//")
        self.assertTrue(present({"kind": "file", "path": "plugins/rtk.ts"}, {}, self.home)[0])
        self.assertFalse(present({"kind": "file", "path": "plugins/none.ts"}, {}, self.home)[0])
        (self.config_dir / "skills/impeccable").mkdir(parents=True)
        self.assertTrue(present({"kind": "skill", "name": "impeccable"}, {}, self.home)[0])

    def test_symlinked_skill_is_present_and_dangling_is_missing(self):
        present = self.check.integration_present
        target = self.home / "agents/caveman"
        target.mkdir(parents=True)
        skills = self.config_dir / "skills"
        skills.mkdir()
        os.symlink(target, skills / "caveman")
        os.symlink(self.home / "agents/gone", skills / "dangling")
        self.assertTrue(present({"kind": "skill", "name": "caveman"}, {}, self.home)[0])
        self.assertFalse(present({"kind": "skill", "name": "dangling"}, {}, self.home)[0])

    def test_version_from_reads_cache_package_json(self):
        cache = self.home / ".cache/opencode/npm/pkg@latest/1"
        cache.mkdir(parents=True)
        (cache / "package.json").write_text(json.dumps({"version": "2.3.4"}))
        spec = {"kind": "plugin", "match": "pkg", "version_from": "pkg@latest/*/package.json"}
        self.assertEqual(self.check.integration_present(spec, {"plugins": ["pkg"]}, self.home), (True, "2.3.4"))

    def test_no_version_source_returns_none(self):
        spec = {"kind": "plugin", "match": "pkg"}
        self.assertEqual(self.check.integration_present(spec, {"plugins": ["pkg"]}, self.home), (True, None))


if __name__ == "__main__":
    unittest.main()
