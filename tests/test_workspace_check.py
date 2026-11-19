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


class UpstreamTest(unittest.TestCase):
    def setUp(self):
        self.check = load()
        self.urls = []

    def fetch(self, body):
        def fetch(url):
            self.urls.append(url)
            if isinstance(body, Exception):
                raise body
            return body
        return fetch

    def test_npm_url_and_version(self):
        latest = self.check.latest_version({"type": "npm", "package": "@scope/tool"}, self.fetch('{"version": "1.4.0"}'))
        self.assertEqual(latest, "1.4.0")
        self.assertEqual(self.urls, ["https://registry.npmjs.org/@scope%2Ftool/latest"])

    def test_github_release_tag(self):
        latest = self.check.latest_version({"type": "github", "repo": "o/r"}, self.fetch('{"tag_name": "v2.0.1"}'))
        self.assertEqual(latest, "v2.0.1")
        self.assertEqual(self.urls, ["https://api.github.com/repos/o/r/releases/latest"])

    def test_raw_takes_the_first_version(self):
        spec = {"type": "raw", "url": "https://raw.githubusercontent.com/o/r/main/package.json"}
        self.assertEqual(self.check.latest_version(spec, self.fetch('{"name": "x", "version": "3.1.2"}')), "3.1.2")

    def test_unlisted_host_is_refused_before_fetch(self):
        fetch = self.fetch("{}")
        for url in ("https://evil.test/x", "http://registry.npmjs.org/x", "https://registry.npmjs.org.evil.test/x"):
            with self.assertRaises(LookupError) as raised:
                self.check.latest_version({"type": "raw", "url": url}, fetch)
            self.assertEqual(str(raised.exception), "host_not_allowed")
        self.assertEqual(self.urls, [])

    def test_unknown_type_is_unsupported(self):
        with self.assertRaises(LookupError) as raised:
            self.check.latest_version({"type": "ftp"}, self.fetch("{}"))
        self.assertEqual(str(raised.exception), "unsupported_source")

    def test_fetch_error_is_lookup_failed(self):
        with self.assertRaises(LookupError) as raised:
            self.check.latest_version({"type": "npm", "package": "x"}, self.fetch(OSError("offline")))
        self.assertEqual(str(raised.exception), "lookup_failed")

    def test_bad_bodies_are_bad_response(self):
        for body in ("<html>rate limited</html>", "{}", '{"version": "latest"}', "[1]"):
            with self.assertRaises(LookupError) as raised:
                self.check.latest_version({"type": "npm", "package": "x"}, self.fetch(body))
            self.assertEqual(str(raised.exception), "bad_response", body)

    def test_redirect_to_unlisted_host_is_refused(self):
        import urllib.error
        import urllib.request
        handler = self.check.AllowedRedirects()
        request = urllib.request.Request("https://api.github.com/repos/o/r/releases/latest")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.test/x")
        allowed = handler.redirect_request(request, None, 301, "Moved", {}, "https://api.github.com/repos/o/r2/releases/latest")
        self.assertEqual(allowed.full_url, "https://api.github.com/repos/o/r2/releases/latest")


if __name__ == "__main__":
    unittest.main()
