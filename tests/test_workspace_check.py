"""Exercise the read-only workspace check with injected probes, never the network."""
import contextlib
import http.client
import importlib.util
import io
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

    def test_prerelease_sorts_below_its_release(self):
        compare = self.check.compare
        self.assertEqual(compare("1.0.0-rc1", "1.0.0"), -1)
        self.assertEqual(compare("1.0.0", "1.0.0-rc1"), 1)
        self.assertEqual(compare("1.0.0-rc1", "1.0.0-rc2"), 0)
        self.assertEqual(compare("git version 2.39.5 (Apple Git-154)", "2.39.5"), 0)

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

    def test_skill_version_from_frontmatter(self):
        skill = self.config_dir / "skills/impeccable"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: impeccable\nversion: 4.10.0\n---\nbody version: 9.9.9\n")
        spec = {"kind": "skill", "name": "impeccable", "version_from": "frontmatter"}
        self.assertEqual(self.check.integration_present(spec, {}, self.home), (True, "4.10.0"))


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

    def test_raw_without_a_version_key_is_a_bad_response(self):
        spec = {"type": "raw", "url": "https://raw.githubusercontent.com/o/r/main/package.json"}
        for body in ("<html>Error 404 page</html>", '{"engines": {"node": ">=18"}}'):
            with self.assertRaises(LookupError) as raised:
                self.check.latest_version(spec, self.fetch(body))
            self.assertEqual(str(raised.exception), "bad_response", body)
        self.assertEqual(self.check.latest_version(spec, self.fetch("v2.1.0\n")), "v2.1.0")

    def test_truncated_download_is_lookup_failed(self):
        with self.assertRaises(LookupError) as raised:
            self.check.latest_version({"type": "npm", "package": "x"}, self.fetch(http.client.IncompleteRead(b"")))
        self.assertEqual(str(raised.exception), "lookup_failed")

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
        with self.assertRaises(urllib.error.HTTPError) as raised:
            handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, "https://evil.test/x")
        raised.exception.close()
        allowed = handler.redirect_request(request, None, 301, "Moved", {}, "https://api.github.com/repos/o/r2/releases/latest")
        self.assertEqual(allowed.full_url, "https://api.github.com/repos/o/r2/releases/latest")


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.check = load()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        (self.home / ".config/opencode/plugins").mkdir(parents=True)

    def env(self, commands=None, pages=None):
        def run(argv):
            answer = (commands or {})[argv[0]]
            if isinstance(answer, Exception):
                raise answer
            return answer

        def fetch(url):
            answer = (pages or {})[url]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return self.check.Env(home=self.home, run=run, fetch=fetch)

    RTK = {"id": "rtk", "why": "Compact output.", "docs": "https://x.test",
           "machine": {"command": "rtk", "version_args": ["--version"]},
           "clients": {"opencode": {"kind": "file", "path": "plugins/rtk.ts"}},
           "upstream": {"type": "github", "repo": "rtk-ai/rtk"}}
    RTK_PAGE = "https://api.github.com/repos/rtk-ai/rtk/releases/latest"

    def hook(self):
        (self.home / ".config/opencode/plugins/rtk.ts").write_text("//")

    def test_rtk_binary_without_hook_is_missing(self):
        row = self.check.judge(self.RTK, {}, self.env({"rtk": "rtk 1.1.0"}, {self.RTK_PAGE: '{"tag_name": "v1.1.0"}'}))
        self.assertEqual((row["state"], row["reason"]), ("missing", "integration_missing"))

    def test_rtk_with_hook_and_current_version_is_ready(self):
        self.hook()
        row = self.check.judge(self.RTK, {}, self.env({"rtk": "rtk 1.1.0"}, {self.RTK_PAGE: '{"tag_name": "v1.1.0"}'}))
        self.assertEqual((row["state"], row["installed"], row["upstream"], row["reason"]), ("ready", "1.1.0", "1.1.0", None))
        self.assertEqual((row["id"], row["why"]), ("rtk", "Compact output."))

    def test_behind_upstream_is_outdated(self):
        self.hook()
        row = self.check.judge(self.RTK, {}, self.env({"rtk": "rtk 1.1.0"}, {self.RTK_PAGE: '{"tag_name": "v1.2.0"}'}))
        self.assertEqual((row["state"], row["upstream"], row["reason"]), ("outdated", "1.2.0", "behind_upstream"))

    def test_installed_prerelease_is_behind_its_release(self):
        self.hook()
        row = self.check.judge(self.RTK, {}, self.env({"rtk": "rtk 1.2.0-rc1"}, {self.RTK_PAGE: '{"tag_name": "v1.2.0"}'}))
        self.assertEqual((row["state"], row["installed"], row["reason"]), ("outdated", "1.2.0", "behind_upstream"))

    def test_lookup_failure_is_unknown_with_reason(self):
        self.hook()
        row = self.check.judge(self.RTK, {}, self.env({"rtk": "rtk 1.1.0"}, {self.RTK_PAGE: OSError("offline")}))
        self.assertEqual((row["state"], row["reason"]), ("unknown", "lookup_failed"))

    def test_absent_or_broken_command(self):
        for failure, state, reason in ((FileNotFoundError(), "missing", "command_not_found"),
                                       (TimeoutError(), "unknown", "command_failed"),
                                       ("no digits here", "unknown", "unreadable_version")):
            row = self.check.judge(self.RTK, {}, self.env({"rtk": failure}))
            self.assertEqual((row["state"], row["reason"]), (state, reason), failure)

    def test_minimum_only_tools_never_ask_upstream(self):
        git = {"id": "git", "why": "Branches.", "docs": "https://x.test",
               "machine": {"command": "git", "version_args": ["--version"]},
               "upstream": {"type": "minimum", "version": "2.30"}}
        apple = "git version 2.39.5 (Apple Git-154)"
        row = self.check.judge(git, {}, self.env({"git": apple}))
        self.assertEqual((row["state"], row["installed"], row["upstream"]), ("ready", "2.39.5", "2.30"))
        git["upstream"]["version"] = "2.40"
        row = self.check.judge(git, {}, self.env({"git": apple}))
        self.assertEqual((row["state"], row["reason"]), ("outdated", "below_minimum"))

    def test_hosted_presence_only_partner(self):
        context7 = {"id": "context7", "why": "Docs.", "docs": "https://x.test",
                    "clients": {"opencode": {"kind": "mcp", "name": "context7"}}, "upstream": {"type": "hosted"}}
        self.assertEqual(self.check.judge(context7, {"mcp": {"servers": {"context7": {}}}}, self.env())["state"], "ready")
        self.assertEqual(self.check.judge(context7, {}, self.env())["state"], "missing")

    def test_unreadable_config_is_unknown_not_missing(self):
        row = self.check.judge({**self.RTK, "machine": None}, None, self.env())
        self.assertEqual((row["state"], row["reason"]), ("unknown", "config_unreadable"))

    def test_no_comparable_local_version_and_no_source(self):
        plugin = {"id": "caveman", "why": "Terse.", "docs": "https://x.test",
                  "clients": {"opencode": {"kind": "plugin", "match": "caveman"}},
                  "upstream": {"type": "github", "repo": "o/caveman"}}
        row = self.check.judge(plugin, {"plugins": ["./plugins/caveman"]}, self.env())
        self.assertEqual((row["state"], row["reason"]), ("unknown", "no_comparable_version"))
        plugin["upstream"] = {"type": "none"}
        row = self.check.judge(plugin, {"plugins": ["./plugins/caveman"]}, self.env())
        self.assertEqual((row["state"], row["reason"]), ("unknown", "no_upstream_source"))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.check = load()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.config_dir = self.home / ".config/opencode"
        self.partners = self.home / "partners.json"
        self.partners.write_text(json.dumps({"version": 1, "partners": [
            {"id": "superpowers", "why": "Skills.", "docs": "https://x.test",
             "clients": {"opencode": {"kind": "plugin", "match": "superpowers"}},
             "upstream": {"type": "hosted"}}]}))

    def run_main(self, *extra):
        env = self.check.Env(home=self.home, run=lambda argv: "", fetch=lambda url: "")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.check.main(["--partners", str(self.partners), *extra], env)
        return code, out.getvalue()

    def test_main_prints_rows_and_exit_code(self):
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "opencode.json").write_text('{"plugins": ["superpowers@git+https://x.test/s.git"]}')
        code, text = self.run_main("--json")
        report = json.loads(text)
        self.assertEqual((code, report["client"], [r["state"] for r in report["rows"]]), (0, "opencode", ["ready"]))
        self.assertIn("superpowers", self.run_main()[1])

    def test_no_config_file_means_integrations_missing(self):
        code, text = self.run_main("--json")
        self.assertEqual((code, json.loads(text)["rows"][0]["state"]), (1, "missing"))

    def test_both_config_files_are_read(self):
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "opencode.json").write_text('{"plugins": ["other"]}')
        (self.config_dir / "opencode.jsonc").write_text('{"plugins": ["superpowers"], "mcp": {"servers": {"a": {}}}}')
        self.assertEqual(json.loads(self.run_main("--json")[1])["rows"][0]["state"], "ready")
        (self.config_dir / "opencode.json").write_text('{"mcp": {"servers": {"b": {}}}}')
        merged = self.check._load_config(self.home)
        self.assertEqual(sorted(merged["mcp"]["servers"]), ["a", "b"])
        self.assertEqual(merged["plugins"], ["superpowers"])

    def test_garbage_config_is_unknown(self):
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "opencode.jsonc").write_text("not json")
        code, text = self.run_main("--json")
        self.assertEqual((code, json.loads(text)["rows"][0]["reason"]), (2, "config_unreadable"))

    def test_check_writes_nothing(self):
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "opencode.json").write_text("{}")

        def snapshot():
            return sorted((str(p), p.stat().st_mtime_ns, p.is_dir() or p.stat().st_size) for p in self.home.rglob("*"))
        before = snapshot()
        self.run_main("--json")
        self.assertEqual(snapshot(), before)

    def test_shipped_partners_json_is_valid(self):
        shipped = json.loads((SCRIPT.parent.parent / "partners.json").read_text())
        ids = [p["id"] for p in shipped["partners"]]
        self.assertEqual(ids, ["superpowers", "ponytail", "caveman", "impeccable", "context7", "rtk",
                               "graphify-cli", "graphify-skill", "git", "gh", "python"])
        for partner in shipped["partners"]:
            self.assertTrue(partner["why"] and partner["docs"].startswith("https://"), partner["id"])
            upstream = partner["upstream"]
            if upstream["type"] in ("npm", "github", "raw"):
                self.assertTrue(self.check.latest_version(upstream, lambda url: self.assertTrue(
                    self.check._allowed(url), url) or '{"version": "1", "tag_name": "1"}'))


if __name__ == "__main__":
    unittest.main()
