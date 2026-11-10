"""Workspace readiness tests: requirements list, client listings, per-client states, record and CLI.

No real client, tool, network or home directory: every command is a fake executable on a temporary
PATH, config files live in a temporary HOME and the record in a temporary XDG state directory."""

import atexit
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import hook_delivery as hd
from runtime import package_versions as pv
from runtime import workspace as ws
from runtime import workspace_observe as ob


PACKAGE = Path(__file__).resolve().parents[1]
VERSION = pv.package_version(PACKAGE)
HOOK_EVENTS = {"SessionStart": "session_start", "PreToolUse": "pre_tool_use", "PostToolUse": "post_tool_use",
               "Stop": "stop", "Interrupt": "interrupt"}
RTK_SHOW = """rtk Configuration:

[ok] Hook: rtk hook claude (native binary command)
[ok] RTK.md: /home/u/.claude/RTK.md (slim mode)
[ok] Global (~/.claude/CLAUDE.md): @RTK.md reference
[--] Local (./CLAUDE.md): not found
[ok] settings.json: RTK hooks configured (PreToolUse)
[ok] OpenCode: plugin installed
[--] Cursor hook: not found

Usage:
  rtk init -g
"""
SIGNED_OUT = "You are not logged into any GitHub hosts. To log in, run: gh auth login\n"


def codex_entry(name, marketplace="market", version="1.0.0", enabled=True, installed=True):
    return {"pluginId": f"{name}@{marketplace}", "name": name, "marketplaceName": marketplace, "version": version,
            "installed": installed, "enabled": enabled, "source": {"source": "local", "path": "/x"},
            "installPolicy": "AVAILABLE", "authPolicy": "ON_INSTALL"}


def claude_entry(name, marketplace="market", version="1.0.0", enabled=True):
    return {"id": f"{name}@{marketplace}", "version": version, "scope": "user", "enabled": enabled,
            "installedAt": "2026-11-08T14:30:39.890Z", "projectEnabled": False}


def codex_plugins(*extra, vibe=None, skip=()):
    entries = [codex_entry(name) for name in ("superpowers", "ponytail", "caveman", "documents") if name not in skip]
    return json.dumps({"installed": [*entries, *([codex_entry("vibe-workflow", "vibe-workflow-local", VERSION)] if vibe is None else vibe), *extra],
                       "available": []})


def claude_plugins(*extra, vibe=None, skip=()):
    entries = [claude_entry(name) for name in ("superpowers", "ponytail", "caveman", "postman") if name not in skip]
    return json.dumps([*entries, *([claude_entry("vibe-workflow", "vibe-workflow-local", VERSION)] if vibe is None else vibe), *extra])


CODEX_MCP = json.dumps([
    {"name": "chrome-devtools", "enabled": True, "transport": {"type": "stdio", "command": "node", "args": ["chrome-devtools-mcp"]}, "auth_status": "unsupported"},
    {"name": "context7", "enabled": True, "transport": {"type": "streamable_http", "url": "https://mcp.context7.com/mcp"}, "auth_status": "o_auth"}])
SECRET = "sk-live-do-not-print"
CLAUDE_STATE = {"numStartups": 3, "mcpServers": {
    "context7": {"type": "http", "url": "https://mcp.context7.com/mcp", "headers": {"Authorization": "Bearer " + SECRET}},
    "chrome-devtools": {"type": "stdio", "command": "npx", "args": ["chrome-devtools-mcp"], "env": {"TOKEN": SECRET}}}}
OPENCODE_CONFIG = {"plugins": [], "mcp": {"context7": {"type": "remote", "url": "https://mcp.context7.com/mcp", "headers": {"X-Key": SECRET}}}}
OPENCODE_PLUGINS = ("ID                              VERSION  SOURCE\n-                               0.3.0    opencode-next-prompt\n"
                    "superpowers                     7.0.2    git+https://github.com/obra/superpowers.git\n"
                    "ponytail                        5.3.3    ponytail\ncaveman                         2.7.0    caveman\n")


GENERIC = """#!/bin/sh
here=$(dirname "$0")
name=$(basename "$0")
key=$(echo "$*" | tr -c 'a-zA-Z0-9\\n' '_')
echo "$*" >> "$here/$name.calls"
file="$here/.$name.$key"
if [ -f "$file.out" ]; then
  [ -f "$file.sleep" ] && sleep "$(cat "$file.sleep")"
  cat "$file.out"
  exit "$(cat "$file.code")"
fi
echo "unexpected: $*" >&2
exit 64
"""
UTILITIES = ("cat", "sleep", "tr", "dirname", "basename")
_shared = tempfile.TemporaryDirectory(prefix="vibe fake cli ")
atexit.register(_shared.cleanup)
FAKE = Path(_shared.name) / "fake"
FAKE.write_text(GENERIC)
FAKE.chmod(0o755)


class World:
    """A temporary machine: fake executables on PATH, a HOME with client config and a state directory.

    Every fake command is a link to one generic script that answers from files, so each test stays fast."""

    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe workspace ")
        self.root = Path(self.temp.name).resolve()
        self.bin, self.home, self.state = self.root / "bin", self.root / "home", self.root / "state"
        for path in (self.bin, self.home):
            path.mkdir()
        # PATH holds only the fake tools and the utilities the generic script calls, so no real client or tool is reachable.
        for utility in UTILITIES:
            (self.bin / utility).symlink_to(shutil.which(utility))
        self.fakes = set()
        self.cli("python3", "--version", "Python 3.12.1")
        self.cli("git", "--version", "git version 2.50.1")
        self.cli("gh", "--version", "gh version 2.103.3 (2026-10-26)")
        self.cli("gh", "auth status", "Logged in to github.com\n")
        self.cli("graphify", "--version", "graphify 0.9.104")
        self.cli("rtk", "--version", "rtk 1.1.0")
        self.cli("rtk", "init --show", RTK_SHOW)
        self.cli("codex", "--version", "codex-cli 0.167.0")
        self.cli("codex", "plugin list --json", codex_plugins())
        self.cli("codex", "mcp list --json", CODEX_MCP)
        self.cli("claude", "--version", "2.1.311 (Claude Code)")
        self.cli("claude", "plugin list --json", claude_plugins())
        self.cli("opencode", "--version", "v2.2.6")
        self.cli("opencode", "plugin list", OPENCODE_PLUGINS)
        self.codex, self.claude, self.opencode = self.home / ".codex", self.home / ".claude", self.home / ".config" / "opencode"
        self.write(self.codex / "RTK.md", "rtk")
        self.write(self.codex / "AGENTS.md", "@RTK.md\n")
        self.write(self.codex / "hooks.json", json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "rtk hook codex"}]}]}}))
        self.trust()
        self.write(self.home / ".claude.json", json.dumps(CLAUDE_STATE))
        self.write(self.opencode / "opencode.json", json.dumps(OPENCODE_CONFIG))
        for home in (self.codex, self.claude):
            self.write(home / "skills/graphify/SKILL.md", "graphify")
        self.write(self.opencode / "skill/graphify/SKILL.md", "graphify")

    def cli(self, name, args, out="", code=0, sleep=0):
        """Make `name args` print `out`, exit `code` and optionally sleep first."""
        if name not in self.fakes:
            (self.bin / name).symlink_to(FAKE)
            self.fakes.add(name)
        base = self.bin / f".{name}.{re.sub(r'[^a-zA-Z0-9]', '_', args)}"
        for suffix, value in (("out", out), ("code", str(code)), ("sleep", str(sleep))):
            target = Path(f"{base}.{suffix}")
            if suffix == "sleep" and not sleep:
                target.unlink(missing_ok=True)
            else:
                target.write_text(value)

    def drop(self, name, args=None):
        if args is None:
            (self.bin / name).unlink(missing_ok=True)
            self.fakes.discard(name)
        else:
            for path in self.bin.glob(f".{name}.{re.sub(r'[^a-zA-Z0-9]', '_', args)}.*"):
                path.unlink()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def trust(self, skip=(), marketplace="vibe-workflow-local", extra="", rtk=True):
        """Codex's hook approvals: the plugin's hooks (both hashes) and, optionally, RTK's user hook (trusted_hash)."""
        lines = [f'[hooks.state."vibe-workflow@{marketplace}:hooks/codex.json:{name}:0:0"]\ntrusted_hash = "sha256:abc"\n'
                 f'trusted_content_hash = "sha256:def"\n{extra}' for name in HOOK_EVENTS.values() if name not in skip]
        if rtk:
            lines.append(f'[hooks.state."{self.codex / "hooks.json"}:pre_tool_use:0:0"]\ntrusted_hash = "sha256:rtk"\n')
        self.write(self.codex / "config.toml", "\n".join(lines))

    def environ(self, **extra):
        return dict(HOME=str(self.home), XDG_STATE_HOME=str(self.state), PATH=str(self.bin), **extra)

    def calls(self, name):
        path = self.bin / (name + ".calls")
        return path.read_text().splitlines() if path.exists() else []

    def status(self, client=None, budget=20, **extra):
        return ws.status(PACKAGE, self.environ(**extra), client=client, budget=budget)

    def states(self, report):
        return {item["id"]: {key: value["state"] for key, value in item["cells"].items()} for item in report["items"]}

    def tree(self):
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file() and path.parent != self.bin)


class WorldTestCase(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.temp.cleanup)

    def cell(self, report, item, key):
        return next(entry for entry in report["items"] if entry["id"] == item)["cells"][key]


class CatalogTests(unittest.TestCase):
    def test_markdown_view_matches_the_json_source_by_id_name_scope_and_why(self):
        catalog = ws.load_catalog(PACKAGE)
        text = (PACKAGE / "clients/workspace-requirements.md").read_text()
        rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in text.splitlines()
                if line.startswith("| ") and not line.startswith("| Item") and not line.startswith("| ---")]
        scope_text = {"machine": "machine", "client": "each client"}
        self.assertEqual(rows, [[item["name"], f"`{item['id']}`", scope_text[item["scope"]], item["why"]] for item in catalog["items"]])

    def test_items_are_complete_unique_and_cover_every_client(self):
        catalog = ws.load_catalog(PACKAGE)
        ids = [item["id"] for item in catalog["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(catalog["clients"], ["codex", "claude-code", "opencode"])
        kinds = {"cli", "package", "hooks-trust", "plugin", "mcp", "rtk", "skill", "unobservable", "not-supported"}
        for item in catalog["items"]:
            with self.subTest(item=item["id"]):
                self.assertTrue(item["install_ref"] and isinstance(item["user_actions"], list))
                specs = [item["check"]] if item["scope"] == "machine" else [item["check"][name] for name in catalog["clients"]]
                self.assertEqual(len(specs), 1 if item["scope"] == "machine" else 3)
                for spec in specs:
                    self.assertIn(spec["kind"], kinds)
                    if spec["kind"] in ("not-supported", "unobservable"):
                        self.assertTrue(spec["reason"])

    def test_required_items_are_the_plan_list_and_name_no_docker_or_node_foundation(self):
        catalog = ws.load_catalog(PACKAGE)
        self.assertEqual({item["id"] for item in catalog["items"]}, {
            "python", "git", "gh", "vibe-workflow-plugin", "vibe-workflow-hooks", "superpowers", "ponytail", "caveman",
            "rtk", "context7", "graphify-cli", "graphify-skill"})
        for name in ("workspace-requirements.md", "workspace-requirements.json"):
            text = (PACKAGE / "clients" / name).read_text().lower()
            self.assertNotIn("docker", text)
            self.assertNotIn("node.js", text)

    def test_hook_trust_keys_follow_the_package_hook_events(self):
        events = json.loads((PACKAGE / "hooks/codex.json").read_text())["hooks"]
        self.assertEqual(set(events), set(HOOK_EVENTS))
        self.assertEqual(sorted(ws._hook_keys(PACKAGE)), sorted(f"{name}:0:0" for name in HOOK_EVENTS.values()))

    def test_toolkit_revision_is_a_full_hash_shown_in_the_markdown_view(self):
        revision = ws.load_catalog(PACKAGE)["toolkit_revision"]
        self.assertRegex(revision, r"^[0-9a-f]{40}$")
        self.assertIn(f"`{revision}`", (PACKAGE / "clients/workspace-requirements.md").read_text())

    def test_unusable_catalogs_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            (package / "clients").mkdir()
            for text in ("{", "[]", json.dumps({"schema_version": 2, "clients": [], "items": []}),
                         json.dumps({"schema_version": 1, "clients": ["codex", "claude-code", "opencode"], "items": ["x"]}),
                         json.dumps(dict(json.loads((PACKAGE / "clients/workspace-requirements.json").read_text()), toolkit_revision="7ef68dd")),
                         json.dumps({k: v for k, v in json.loads((PACKAGE / "clients/workspace-requirements.json").read_text()).items()
                                     if k != "toolkit_revision"})):
                (package / "clients/workspace-requirements.json").write_text(text)
                with self.assertRaises(ValueError):
                    ws.load_catalog(package)


class ParserTests(unittest.TestCase):
    def test_codex_plugin_listing_keeps_installed_entries(self):
        entries = ob.codex_plugins(codex_plugins(codex_entry("gone", installed=False), codex_entry("off", enabled=False)))
        self.assertEqual([(sorted(entry["names"]), entry["enabled"]) for entry in entries],
                         [(["superpowers"], True), (["ponytail"], True), (["caveman"], True), (["documents"], True),
                          (["vibe-workflow"], True), (["off"], False)])

    def test_claude_plugin_listing_keeps_the_servers_a_plugin_declares(self):
        entries = ob.claude_plugins(claude_plugins(dict(claude_entry("mirror"), mcpServers={"Context7": {"url": "https://x"}})))
        self.assertEqual([sorted(entry["names"]) for entry in entries][:3], [["superpowers"], ["ponytail"], ["caveman"]])
        self.assertEqual(entries[-1]["servers"], ["context7"])

    def test_spec_names_use_the_last_path_segment_without_git_suffix_or_version(self):
        cases = {"superpowers@git+https://github.com/obra/superpowers.git": "superpowers", "@dietrichgebert/ponytail": "ponytail",
                 "@upstash/context7-opencode": "context7-opencode", "ponytail@1.2.3": "ponytail", "git+https://x.invalid/a/Caveman.git#main": "caveman",
                 "git@github.com:obra/superpowers.git": "superpowers", "not-superpowers.git": "not-superpowers", "caveman-extras": "caveman-extras",
                 "@scope/pkg@2.0.0": "pkg"}
        for spec, name in cases.items():
            self.assertEqual(ob.spec_name(spec), name, spec)

    def test_opencode_table_rows_match_by_exact_id_or_source_name(self):
        rows = ob.opencode_plugins(OPENCODE_PLUGINS)
        self.assertEqual(len(rows), 4)
        self.assertEqual([ob.plugin_state(rows, [name])[0] for name in ("superpowers", "ponytail", "caveman", "context7")],
                         ["ready", "ready", "ready", "missing"])
        self.assertEqual(ob.opencode_plugins("ID  VERSION  SOURCE\n"), [])

    def test_lookalike_plugins_do_not_count(self):
        table = ("ID  VERSION  SOURCE\ncaveman-extras  1.0.0  caveman-extras\nnot-superpowers.git  1.0.0  git+https://x.invalid/not-superpowers.git\n"
                 "my-context7-mirror  1.0.0  my-context7-mirror\n")
        rows = ob.opencode_plugins(table)
        for name in ("caveman", "superpowers", "context7"):
            self.assertEqual(ob.plugin_state(rows, [name])[0], "missing", name)
        entries = ob.codex_plugins(codex_plugins(codex_entry("caveman-extras"), skip=("caveman",)))
        self.assertEqual(ob.plugin_state(entries, ["caveman"])[0], "missing")

    def test_a_disabled_copy_is_not_hidden_by_an_enabled_one(self):
        entries = ob.codex_plugins(codex_plugins(codex_entry("ponytail", "other", enabled=False)))
        self.assertEqual(ob.plugin_state(entries, ["ponytail"]), ("missing", "installed but disabled"))

    def test_unrecognized_plugin_output_is_none_never_empty(self):
        for text in ("", "oops", "null", "{}", '{"installed": {}}', '{"installed": [5]}', '{"installed": [{"pluginId": "a@b"}]}', "{" * 5000):
            self.assertIsNone(ob.codex_plugins(text), text[:20])
        for text in ("", "oops", "{}", "[5]", '[{"id": "a@b"}]', '[{"id": 5, "enabled": true}]'):
            self.assertIsNone(ob.claude_plugins(text), text[:20])
        for text in ("", "   \n", "NAME  STATUS\nx  y\n", "garbled \x00", "Error: database is locked\n", "No plugins installed\n", "ID  VERSION  SOURCE\nbroken\n"):
            self.assertIsNone(ob.opencode_plugins(text), text[:20])

    def test_codex_mcp_listing(self):
        self.assertEqual([(entry["name"], entry["status"]) for entry in ob.codex_mcp(CODEX_MCP)], [("chrome-devtools", "ok"), ("context7", "ok")])
        disabled = json.dumps([{"name": "context7", "enabled": False, "transport": {}}])
        self.assertEqual(ob.codex_mcp(disabled)[0]["status"], "disabled")
        signed_out = json.dumps([{"name": "context7", "enabled": True, "transport": {}, "auth_status": "not_logged_in"}])
        self.assertEqual(ob.codex_mcp(signed_out)[0]["status"], "needs-auth")
        self.assertEqual(ob.codex_mcp("[]"), [])
        for text in ("", "null", "{}", '[{"name": 1, "enabled": true}]', '[{"name": "x"}]', "Error: boom"):
            self.assertIsNone(ob.codex_mcp(text), text)

    def test_mcp_servers_match_the_exact_name_only(self):
        names = ["context7"]
        for lookalike in ("my-context7-mirror", "context7-mcp-fork", "context7-extra", "not-context7"):
            self.assertEqual(ob.mcp_state([dict(name=lookalike, status="ok")], names)[0], "missing", lookalike)
        self.assertEqual(ob.mcp_state([dict(name="context7", status="ok")], names), ("ready", None))
        self.assertEqual(ob.mcp_state([], names)[0], "missing")

    def test_a_failing_server_is_not_hidden_by_a_passing_one_of_the_same_name(self):
        both = lambda status: [dict(name="context7", status="ok"), dict(name="context7", status=status)]
        self.assertEqual(ob.mcp_state(both("disabled"), ["context7"])[0], "missing")
        self.assertEqual(ob.mcp_state(both("needs-auth"), ["context7"])[0], "needs-user")

    def test_jsonc_comments_and_trailing_commas_are_tolerated(self):
        text = '{\n // note\n "mcp": {"context7": {"url": "https://x//y", /* c */ "enabled": true,},},\n}'
        self.assertEqual(json.loads(ob.strip_jsonc(text))["mcp"]["context7"]["url"], "https://x//y")
        with self.assertRaises(ValueError):
            ob.strip_jsonc("{ /* never closed }")

    def test_rtk_marks_require_the_header(self):
        self.assertEqual(ob.rtk_marks(RTK_SHOW)["Hook"], True)
        self.assertEqual(ob.rtk_marks(RTK_SHOW)["Cursor hook"], False)
        self.assertIsNone(ob.rtk_marks("error: unrecognized subcommand"))
        self.assertIsNone(ob.rtk_marks("rtk Configuration:\n\nnothing recognizable\n"))


class OverallTests(unittest.TestCase):
    def result(self, *states):
        return ws.overall([dict(cells={f"c{index}": ws.cell(state, "why") for index, state in enumerate(states)})])

    def test_ready_needs_every_cell_ready_or_not_supported(self):
        self.assertEqual(self.result("ready", "not-supported"), "ready")
        self.assertEqual(self.result("ready", "unknown"), "undetermined")
        self.assertEqual(self.result("ready", "unknown", "missing"), "not-ready")
        for state in ("missing", "outdated", "needs-user"):
            self.assertEqual(self.result("ready", state), "not-ready")
        self.assertEqual({ws.EXIT[value] for value in ("ready", "not-ready", "undetermined")}, {0, 1, 2})


class StatusTests(WorldTestCase):
    def test_everything_installed_leaves_only_what_cannot_be_seen_unknown(self):
        report = self.world.status(client="codex")
        states = self.world.states(report)
        self.assertEqual(states["python"], dict(machine="ready"))
        self.assertEqual(states["vibe-workflow-plugin"], dict(codex="ready", **{"claude-code": "ready"}, opencode="not-supported"))
        self.assertEqual(states["vibe-workflow-hooks"], dict(codex="unknown", **{"claude-code": "unknown"}, opencode="not-supported"))
        for item in ("superpowers", "ponytail", "caveman", "rtk", "context7", "graphify-skill"):
            self.assertEqual(set(states[item].values()), {"ready"}, item)
        self.assertEqual((report["result"], report["exit_code"], report["session_client"]), ("undetermined", 2, "codex"))
        self.assertEqual([entry["client"] for entry in report["clients"]], ["codex", "claude-code", "opencode"])
        self.assertEqual(report["clients"][0], dict(client="codex", installed=True, cli_version="0.167.0", config_dir=True, session=True))

    def hooks(self, client="codex", **extra):
        return self.cell(self.world.status(**extra), "vibe-workflow-hooks", client)

    def deliver(self, client):
        self.assertTrue(hd.record_delivery(PACKAGE, client, self.world.environ()))

    def test_hooks_are_ready_only_with_a_matching_delivery_note(self):
        for client in ("codex", "claude-code"):
            with self.subTest(client=client):
                self.assertEqual(self.hooks(client), dict(state="unknown", reason="start a new session"))
                self.deliver(client)
                value = self.hooks(client)
                self.assertEqual(value["state"], "ready")
                self.assertRegex(value["reason"], r"^hooks ran \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        report = self.world.status()
        self.assertEqual(self.world.states(report)["vibe-workflow-hooks"], dict(codex="ready", **{"claude-code": "ready"}, opencode="not-supported"))

    def test_a_note_for_other_hooks_or_another_version_leaves_hooks_unknown(self):
        self.deliver("codex")
        path = hd.note_path("codex", self.world.environ())
        note = json.loads(path.read_text())
        for field, value in (("version", "0.0.1"), ("hook_sha256", "0" * 64), ("client", "claude-code")):
            path.write_text(json.dumps(dict(note, **{field: value})))
            with self.subTest(field=field):
                self.assertEqual(self.hooks()["state"], "unknown")
        path.write_text("{")
        self.assertEqual(self.hooks()["state"], "unknown")

    def test_missing_approvals_stay_needs_user_even_with_a_note_and_disabled_plugin_is_missing(self):
        self.deliver("codex")
        self.world.trust(skip=("stop",))
        self.assertEqual(self.hooks()["state"], "needs-user")
        self.world.trust()
        self.assertEqual(self.hooks()["state"], "ready")
        self.deliver("claude-code")
        self.world.cli("claude", "plugin list --json", claude_plugins(vibe=[claude_entry("vibe-workflow", "vibe-workflow-local", VERSION, enabled=False)]))
        self.assertEqual(self.hooks("claude-code")["state"], "missing")

    def test_an_old_or_future_note_leaves_hooks_unknown(self):
        self.deliver("codex")
        path = hd.note_path("codex", self.world.environ())
        note = json.loads(path.read_text())
        for days, expected in ((6, "ready"), (8, "unknown"), (-1, "unknown")):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
            path.write_text(json.dumps(dict(note, delivered_at=stamp)))
            with self.subTest(days=days):
                value = self.hooks()
                self.assertEqual(value["state"], expected)
                if expected == "unknown":
                    self.assertEqual(value["reason"], "start a new session")

    def test_checking_hooks_writes_nothing(self):
        self.deliver("codex")
        before = self.world.tree()
        self.hooks()
        self.assertEqual(before, self.world.tree())

    def test_hook_state_without_a_note_is_never_ready(self):
        report = self.world.status()
        for key, value in next(item for item in report["items"] if item["id"] == "vibe-workflow-hooks")["cells"].items():
            self.assertNotEqual(value["state"], "ready", key)
            self.assertTrue(value["reason"])

    def test_every_non_ready_cell_has_a_reason(self):
        self.world.cli("opencode", "plugin list", "ID  VERSION  SOURCE\n")
        for item in self.world.status()["items"]:
            for key, value in item["cells"].items():
                self.assertEqual(bool(value.get("reason")), value["state"] != "ready", (item["id"], key))

    def test_missing_item_makes_the_workspace_not_ready(self):
        self.world.cli("codex", "plugin list --json", codex_plugins(skip=("superpowers",)))
        report = self.world.status()
        self.assertEqual(self.cell(report, "superpowers", "codex")["state"], "missing")
        self.assertEqual(self.cell(report, "superpowers", "claude-code")["state"], "ready")
        self.assertEqual((report["result"], report["exit_code"]), ("not-ready", 1))

    def test_disabled_plugin_is_missing_with_reason(self):
        self.world.cli("claude", "plugin list --json", claude_plugins(claude_entry("ponytail", enabled=False), skip=("ponytail",)))
        value = self.cell(self.world.status(), "ponytail", "claude-code")
        self.assertEqual((value["state"], value["reason"]), ("missing", "installed but disabled"))

    def test_vibe_workflow_plugin_states(self):
        self.world.cli("codex", "plugin list --json", codex_plugins(vibe=[codex_entry("vibe-workflow", "m", "0.0.1")]))
        self.world.cli("claude", "plugin list --json", claude_plugins(vibe=[]))
        report = self.world.status()
        self.assertEqual(self.cell(report, "vibe-workflow-plugin", "codex")["state"], "outdated")
        self.assertIn(VERSION, self.cell(report, "vibe-workflow-plugin", "codex")["reason"])
        self.assertEqual(self.cell(report, "vibe-workflow-plugin", "claude-code")["state"], "missing")
        self.world.cli("codex", "plugin list --json", codex_plugins(vibe=[codex_entry("vibe-workflow", "m", VERSION, enabled=False)]))
        self.assertEqual(self.cell(self.world.status(), "vibe-workflow-plugin", "codex")["state"], "missing")

    def test_opencode_plugin_item_is_not_supported_until_its_slice(self):
        report = self.world.status()
        for item in ("vibe-workflow-plugin", "vibe-workflow-hooks"):
            self.assertEqual(self.cell(report, item, "opencode"),
                             dict(state="not-supported", reason="OpenCode plugin client pending (plan slice R4)"))

    def test_machine_tools(self):
        self.world.cli("python3", "--version", "Python 3.10.9")
        self.world.cli("gh", "auth status", SIGNED_OUT, code=1)
        self.world.drop("git")
        report = self.world.status()
        self.assertEqual(self.cell(report, "python", "machine")["state"], "outdated")
        self.assertEqual(self.cell(report, "git", "machine"), dict(state="missing", reason="git is not on PATH"))
        self.assertEqual(self.cell(report, "gh", "machine")["state"], "needs-user")
        self.world.cli("gh", "auth status", "network error\n", code=1)
        self.assertEqual(self.cell(self.world.status(), "gh", "machine")["state"], "unknown")
        self.world.cli("gh", "--version", "no version here")
        self.assertEqual(self.cell(self.world.status(), "gh", "machine"), dict(state="unknown", reason="unrecognized-output"))

    def test_graphify_command_and_skill_are_separate(self):
        self.world.drop("graphify")
        (self.world.codex / "skills/graphify/SKILL.md").unlink()
        report = self.world.status()
        self.assertEqual(self.cell(report, "graphify-cli", "machine")["state"], "missing")
        self.assertEqual(self.cell(report, "graphify-skill", "codex")["state"], "missing")
        self.assertEqual(self.cell(report, "graphify-skill", "opencode")["state"], "ready")

    def test_graphify_skill_is_also_found_in_the_shared_home_locations(self):
        for client in ("codex", "opencode"):
            for path in (self.world.codex, self.world.claude, self.world.opencode):
                for found in path.glob("skill*/graphify/SKILL.md"):
                    found.unlink()
        report = self.world.status()
        self.assertEqual([self.cell(report, "graphify-skill", key)["state"] for key in ("codex", "claude-code", "opencode")], ["missing"] * 3)
        self.world.write(self.world.home / ".agents/skills/graphify/SKILL.md", "graphify")
        report = self.world.status()
        self.assertEqual([self.cell(report, "graphify-skill", key)["state"] for key in ("codex", "claude-code", "opencode")], ["ready", "missing", "ready"])
        (self.world.home / ".agents/skills/graphify/SKILL.md").unlink()
        self.world.write(self.world.home / ".claude/skills/graphify/SKILL.md", "graphify")
        report = self.world.status()
        self.assertEqual([self.cell(report, "graphify-skill", key)["state"] for key in ("codex", "claude-code", "opencode")], ["missing", "ready", "ready"])

    def test_rtk_per_client(self):
        self.world.cli("rtk", "init --show", RTK_SHOW.replace("[ok] OpenCode", "[--] OpenCode").replace("[ok] settings.json", "[--] settings.json"))
        (self.world.codex / "hooks.json").write_text("{}")
        report = self.world.status()
        self.assertEqual([self.cell(report, "rtk", key)["state"] for key in ("codex", "claude-code", "opencode")], ["missing"] * 3)
        self.assertIn("the hook", self.cell(report, "rtk", "codex")["reason"])
        self.world.cli("rtk", "init --show", "something else entirely")
        self.assertEqual(self.cell(self.world.status(), "rtk", "claude-code"), dict(state="unknown", reason="unrecognized-output"))
        self.world.drop("rtk")
        self.assertEqual(self.cell(self.world.status(), "rtk", "claude-code"), dict(state="missing", reason="rtk is not on PATH"))

    def test_rtk_codex_hook_counts_only_when_approved(self):
        self.world.trust(rtk=False)
        value = self.cell(self.world.status(), "rtk", "codex")
        self.assertEqual(value["state"], "needs-user")
        self.assertIn("hook is not approved", value["reason"])
        self.world.trust(rtk=False, extra="")
        (self.world.codex / "config.toml").write_text((self.world.codex / "config.toml").read_text() +
            f'\n[hooks.state."{self.world.codex / "hooks.json"}:pre_tool_use:0:0"]\nenabled = false\ntrusted_hash = "sha256:rtk"\n')
        self.assertEqual(self.cell(self.world.status(), "rtk", "codex")["state"], "needs-user")
        (self.world.codex / "config.toml").unlink()
        self.assertEqual(self.cell(self.world.status(), "rtk", "codex")["state"], "needs-user")
        (self.world.codex / "config.toml").write_text("[broken\n")
        self.assertEqual(self.cell(self.world.status(), "rtk", "codex")["state"], "unknown")

    def test_context7_is_read_from_configuration_never_from_a_connection(self):
        report = self.world.status()
        self.assertEqual({key: value["state"] for key, value in next(i for i in report["items"] if i["id"] == "context7")["cells"].items()},
                         dict(codex="ready", **{"claude-code": "ready"}, opencode="ready"))
        for name in ("claude", "opencode"):
            self.assertFalse([line for line in self.world.calls(name) if "mcp" in line], name)
        self.world.cli("codex", "mcp list --json", json.dumps([]))
        (self.world.home / ".claude.json").write_text(json.dumps({"mcpServers": {"my-context7-mirror": {"url": "x"}, "context7-mcp-fork": {}}}))
        self.world.write(self.world.opencode / "opencode.json", json.dumps({"mcp": {}}))
        report = self.world.status()
        for key in ("codex", "claude-code", "opencode"):
            self.assertEqual(self.cell(report, "context7", key), dict(state="missing", reason="not configured in this client"), key)

    def test_context7_disabled_or_signed_out_is_not_ready(self):
        self.world.cli("codex", "mcp list --json", json.dumps([{"name": "context7", "enabled": True, "transport": {}, "auth_status": "not_logged_in"}]))
        state = dict(CLAUDE_STATE, mcpServers={"context7": {"url": "x", "enabled": False}})
        self.world.write(self.world.home / ".claude.json", json.dumps(state))
        self.world.write(self.world.opencode / "opencode.json", json.dumps({"mcp": {"context7": {"enabled": False}}}))
        report = self.world.status()
        self.assertEqual([self.cell(report, "context7", key)["state"] for key in ("codex", "claude-code", "opencode")], ["needs-user", "missing", "missing"])

    def test_context7_may_come_from_a_claude_plugin_or_an_opencode_plugin(self):
        (self.world.home / ".claude.json").write_text("{}")
        self.world.cli("claude", "plugin list --json", claude_plugins(dict(claude_entry("context7-plugin"), mcpServers={"context7": {"url": "x"}})))
        self.world.write(self.world.opencode / "opencode.json", "{}")
        self.world.cli("opencode", "plugin list", OPENCODE_PLUGINS + "@upstash/context7-opencode  1.0.0  @upstash/context7-opencode\n")
        report = self.world.status()
        self.assertEqual(self.cell(report, "context7", "claude-code")["state"], "ready")
        self.assertEqual(self.cell(report, "context7", "opencode")["state"], "ready")
        self.world.cli("claude", "plugin list --json", "garbage")
        self.assertEqual(self.cell(self.world.status(), "context7", "claude-code"), dict(state="unknown", reason="unrecognized-output"))

    def test_claude_and_opencode_configuration_locations_are_honored(self):
        other = self.world.root / "other-claude"
        self.world.write(other / ".claude.json", json.dumps({"mcpServers": {"context7": {"url": "x"}}}))
        (self.world.home / ".claude.json").write_text("{}")
        self.assertEqual(self.cell(self.world.status(), "context7", "claude-code")["state"], "missing")
        self.assertEqual(self.cell(self.world.status(CLAUDE_CONFIG_DIR=str(other)), "context7", "claude-code")["state"], "ready")
        xdg = self.world.root / "xdg"
        self.world.write(xdg / "opencode/opencode.jsonc", '{\n // servers\n "mcp": {"context7": {"type": "remote", "url": "https://x//y",},},\n}')
        self.assertEqual(self.cell(self.world.status(XDG_CONFIG_HOME=str(xdg)), "context7", "opencode")["state"], "ready")

    def test_unreadable_mcp_configuration_is_unknown(self):
        (self.world.home / ".claude.json").write_text("{not json")
        self.world.write(self.world.opencode / "opencode.json", '{"mcp": ["not", "an", "object"]}')
        report = self.world.status()
        self.assertEqual(self.cell(report, "context7", "claude-code"), dict(state="unknown", reason="the client configuration is not readable"))
        self.assertEqual(self.cell(report, "context7", "opencode")["state"], "unknown")

    def test_no_secret_value_reaches_the_report_or_the_record(self):
        report = self.world.status()
        self.assertNotIn(SECRET, json.dumps(report))
        ws.record(PACKAGE, self.world.environ())
        self.assertNotIn(SECRET, ws.record_path(self.world.environ()).read_text())

    def test_codex_hook_trust_reports_only_what_the_config_shows(self):
        self.world.trust(skip=("stop", "interrupt"))
        value = self.cell(self.world.status(), "vibe-workflow-hooks", "codex")
        self.assertEqual(value["state"], "needs-user")
        self.assertIn("2 hook approvals", value["reason"])
        (self.world.codex / "config.toml").unlink()
        self.assertEqual(self.cell(self.world.status(), "vibe-workflow-hooks", "codex")["state"], "needs-user")
        self.world.trust(marketplace="another-market")
        self.assertEqual(self.cell(self.world.status(), "vibe-workflow-hooks", "codex")["state"], "needs-user")
        (self.world.codex / "config.toml").write_text("[broken\n")
        self.assertEqual(self.cell(self.world.status(), "vibe-workflow-hooks", "codex")["state"], "unknown")

    def test_a_disabled_approval_or_one_without_a_content_hash_is_not_approved(self):
        self.world.trust(extra="enabled = false\n")
        value = self.cell(self.world.status(), "vibe-workflow-hooks", "codex")
        self.assertEqual(value["state"], "needs-user")
        self.assertIn("5 hook approvals", value["reason"])
        config = (self.world.codex / "config.toml")
        self.world.trust()
        config.write_text(config.read_text().replace('trusted_content_hash = "sha256:def"\n', ""))
        self.assertEqual(self.cell(self.world.status(), "vibe-workflow-hooks", "codex")["state"], "needs-user")
        self.world.trust(extra="enabled = true\n")
        self.assertEqual(self.cell(self.world.status(), "vibe-workflow-hooks", "codex")["state"], "unknown")

    def test_session_client_without_a_command_is_unknown_not_assumed_ready(self):
        self.world.drop("opencode")
        self.assertEqual([entry["client"] for entry in self.world.status()["clients"] if entry["installed"]], ["codex", "claude-code"])
        self.assertNotIn("opencode", self.world.status()["items"][3]["cells"])
        report = self.world.status(client="opencode")
        self.assertEqual(self.cell(report, "superpowers", "opencode"), dict(state="unknown", reason="cli-missing"))
        self.assertEqual(self.cell(report, "vibe-workflow-plugin", "opencode")["state"], "not-supported")
        self.assertEqual(report["clients"][2], dict(client="opencode", installed=False, cli_version=None, config_dir=True, session=True))

    def test_no_client_at_all_is_undetermined(self):
        for name in ("codex", "claude", "opencode"):
            self.world.drop(name)
        report = self.world.status()
        self.assertEqual((report["result"], report["exit_code"]), ("undetermined", 2))
        self.assertTrue(report["notes"])

    def test_session_client_comes_from_the_argument_or_one_marker(self):
        self.assertIsNone(self.world.status()["session_client"])
        self.assertEqual(self.world.status(CLAUDECODE="1")["session_client"], "claude-code")
        self.assertEqual(self.world.status(OPENCODE="1")["session_client"], "opencode")
        self.assertEqual(self.world.status(CODEX_THREAD_ID="t")["session_client"], "codex")
        self.assertIsNone(self.world.status(CLAUDECODE="1", OPENCODE="1")["session_client"])
        self.assertEqual(self.world.status(client="codex", CLAUDECODE="1", OPENCODE="1")["session_client"], "codex")
        self.assertIsNone(ob.session_client({}, requested="other"))

    def test_client_config_directories_follow_the_environment(self):
        environ = {"HOME": "/h", "CODEX_HOME": "/c", "CLAUDE_CONFIG_DIR": "/cl", "XDG_CONFIG_HOME": "/x"}
        self.assertEqual([str(ob.client_home(name, environ)) for name in ws.CLIENTS], ["/c", "/cl", "/x/opencode"])
        self.assertEqual([str(ob.client_home(name, {"HOME": "/h"})) for name in ws.CLIENTS], ["/h/.codex", "/h/.claude", "/h/.config/opencode"])

    def test_each_command_runs_once_per_status_and_no_mcp_listing_is_run(self):
        self.world.status()
        used = {}
        for name in ("codex", "claude", "opencode", "rtk", "gh", "git", "python3", "graphify"):
            calls = self.world.calls(name)
            self.assertEqual(len(calls), len(set(calls)), (name, calls))
            used[name] = set(calls)
        self.assertEqual(used["codex"], {"--version", "plugin list --json", "mcp list --json"})
        self.assertEqual(used["claude"], {"--version", "plugin list --json"})
        self.assertEqual(used["opencode"], {"--version", "plugin list"})
        self.assertEqual(used["rtk"], {"--version", "init --show"})
        self.assertEqual(used["gh"], {"--version", "auth status"})

    def test_malformed_outputs_are_unknown_never_missing(self):
        self.world.cli("codex", "plugin list --json", "garbage")
        self.world.cli("claude", "plugin list --json", "{}")
        self.world.cli("opencode", "plugin list", "")
        report = self.world.status()
        for client in ("codex", "claude-code"):
            for item in ("vibe-workflow-plugin", "superpowers", "ponytail", "caveman"):
                self.assertEqual(self.cell(report, item, client), dict(state="unknown", reason="unrecognized-output"), (item, client))
        self.assertEqual(self.cell(report, "superpowers", "opencode")["state"], "unknown")
        self.world.cli("opencode", "plugin list", "Error: database is locked\n")
        self.assertEqual(self.cell(self.world.status(), "ponytail", "opencode"), dict(state="unknown", reason="unrecognized-output"))

    def test_failing_listing_is_unknown(self):
        self.world.cli("codex", "plugin list --json", "boom", code=3)
        self.assertEqual(self.cell(self.world.status(), "ponytail", "codex"), dict(state="unknown", reason="cli-failed"))

    def test_oversized_output_and_unlaunchable_commands_are_unknown(self):
        with patch.object(ob, "MAX_OUTPUT", 10):
            self.assertEqual(self.cell(self.world.status(), "ponytail", "codex"), dict(state="unknown", reason="output-too-large"))
        self.world.drop("claude")
        (self.world.bin / "claude").write_bytes(b"\x00\x01 not a program")  # executable bit set, but it cannot start
        (self.world.bin / "claude").chmod(0o755)
        report = self.world.status()
        self.assertEqual(self.cell(report, "ponytail", "claude-code"), dict(state="unknown", reason="cli-failed"))
        self.assertEqual(self.cell(report, "ponytail", "codex")["state"], "ready")

    def test_endless_output_is_stopped_at_the_cap_not_read_to_the_end(self):
        self.world.drop("claude")
        (self.world.bin / "claude").write_text("#!/bin/sh\nexec yes 0123456789\n")
        (self.world.bin / "claude").chmod(0o755)
        (self.world.bin / "yes").symlink_to(shutil.which("yes"))
        started = time.monotonic()
        report = self.world.status()
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(self.cell(report, "ponytail", "claude-code"), dict(state="unknown", reason="output-too-large"))
        self.assertEqual(self.cell(report, "ponytail", "codex")["state"], "ready")
        self.assertFalse(subprocess.run(["pgrep", "-f", "yes 0123456789"], capture_output=True).stdout)

    def test_unusable_config_files_are_unknown_not_missing(self):
        (self.world.codex / "RTK.md").unlink()
        (self.world.codex / "RTK.md").mkdir()
        self.assertEqual(self.cell(self.world.status(), "rtk", "codex")["state"], "unknown")
        self.world.trust()
        with patch.object(ob, "MAX_CONFIG", 4):
            self.assertEqual(self.cell(self.world.status(), "rtk", "codex")["state"], "unknown")

    def test_reasons_carry_no_local_paths(self):
        self.world.cli("codex", "plugin list --json", codex_plugins(skip=("caveman",)))
        text = json.dumps(self.world.status())
        self.assertNotIn(str(self.world.home), text)
        self.assertNotIn(str(self.world.root), text)

    def test_slow_command_times_out_as_unknown(self):
        self.world.cli("codex", "plugin list --json", codex_plugins(), sleep=5)
        started = time.monotonic()
        with patch.object(ob, "CLI_TIMEOUT", 0.4):
            report = self.world.status(budget=20)
        self.assertLess(time.monotonic() - started, 4)  # the sleeping child did not outlast its timeout
        self.assertEqual(self.cell(report, "superpowers", "codex"), dict(state="unknown", reason="timeout"))
        self.assertEqual(self.cell(report, "superpowers", "claude-code")["state"], "ready")

    def test_spent_time_budget_leaves_remaining_cells_unknown(self):
        for name, args in (("codex", "plugin list --json"), ("claude", "plugin list --json")):
            self.world.cli(name, args, "[]", sleep=30)
        started = time.monotonic()
        report = self.world.status(budget=1.0)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(self.cell(report, "superpowers", "codex"), dict(state="unknown", reason="time-budget"))
        self.assertEqual(self.cell(report, "superpowers", "claude-code"), dict(state="unknown", reason="time-budget"))
        self.assertEqual(self.cell(report, "superpowers", "opencode"), dict(state="unknown", reason="time-budget"))
        self.assertEqual(report["result"], "undetermined")

    def test_status_writes_nothing(self):
        before = self.world.tree()
        self.world.status()
        self.assertEqual(self.world.tree(), before)
        self.assertFalse(self.world.state.exists())


class RecordTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.path = ws.record_path(self.world.environ())

    def test_record_path_is_beside_the_package_record(self):
        self.assertEqual(self.path, self.world.state / "vibe-workflow" / "workspace-readiness.json")
        self.assertEqual(ws.record_path(dict(HOME="/home/u")), Path("/home/u/.local/state/vibe-workflow/workspace-readiness.json"))

    def test_record_stores_a_small_private_summary(self):
        report = ws.record(PACKAGE, self.world.environ())
        self.assertTrue(report["ok"] and report["recorded"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(os.listdir(self.path.parent), [self.path.name])
        record = ws.read_record(self.path)
        self.assertEqual((record["result"], record["package_version"]), ("undetermined", VERSION))
        self.assertEqual(record["cells"]["superpowers"], dict(codex="ready", **{"claude-code": "ready"}, opencode="ready"))
        self.assertRegex(record["observed_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(record["not_ready"], [])
        self.assertLess(self.path.stat().st_size, 3000)

    def test_record_lists_cells_that_are_not_ready(self):
        self.world.cli("codex", "plugin list --json", codex_plugins(skip=("caveman",)))
        ws.record(PACKAGE, self.world.environ())
        self.assertEqual(ws.read_record(self.path)["not_ready"], ["caveman:codex:missing"])
        self.assertEqual(ws.read_record(self.path)["result"], "not-ready")

    def test_existing_shared_state_directory_keeps_its_mode(self):
        self.path.parent.mkdir(parents=True)
        os.chmod(self.path.parent, 0o755)
        self.assertTrue(ws.record(PACKAGE, self.world.environ())["recorded"])
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o755)

    def test_symlinked_or_foreign_state_directory_is_refused_without_writing(self):
        target = self.world.root / "elsewhere"
        target.mkdir()
        self.path.parent.parent.mkdir(parents=True)
        self.path.parent.symlink_to(target, target_is_directory=True)
        report = ws.record(PACKAGE, self.world.environ())
        self.assertEqual((report["ok"], report["recorded"]), (False, False))
        self.assertEqual(os.listdir(target), [])
        self.path.parent.unlink()
        self.path.parent.mkdir()
        with patch("runtime.package_versions.os.getuid", return_value=os.getuid() + 1):
            self.assertFalse(ws.record(PACKAGE, self.world.environ())["recorded"])
        self.assertEqual(os.listdir(self.path.parent), [])

    def test_failed_replace_keeps_the_previous_record(self):
        ws.record(PACKAGE, self.world.environ())
        before = self.path.read_bytes()
        with patch("runtime.workspace.os.replace", side_effect=OSError("disk")):
            self.assertFalse(ws.record(PACKAGE, self.world.environ())["recorded"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(os.listdir(self.path.parent), [self.path.name])

    def test_invalid_records_read_as_none(self):
        self.assertIsNone(ws.read_record(self.path))
        self.path.parent.mkdir(parents=True)
        valid = dict(schema_version=1, package="vibe-workflow", observed_at="2026-11-10T00:00:00Z", result="ready", cells={}, not_ready=[])
        bad = {"not json": b"{", "list": b"[1]", "schema": json.dumps(dict(valid, schema_version=2)).encode(),
               "package": json.dumps(dict(valid, package="x")).encode(), "result": json.dumps(dict(valid, result="fine")).encode(),
               "oversized": json.dumps(dict(valid, pad="x" * ws.MAX_RECORD)).encode(), "binary": b"\xff\x00"}
        for name, raw in bad.items():
            with self.subTest(name=name):
                self.path.write_bytes(raw)
                self.assertIsNone(ws.read_record(self.path))
        self.path.write_text(json.dumps(valid))
        self.assertEqual(ws.read_record(self.path)["result"], "ready")
        self.path.unlink()
        self.path.symlink_to(self.world.root / "nowhere")
        self.assertIsNone(ws.read_record(self.path))


class CommandTests(WorldTestCase):
    def run_cli(self, *args, **extra):
        return subprocess.run([sys.executable, "-B", str(PACKAGE / "scripts/workflow.py"), "workspace", *args],
                              capture_output=True, text=True, timeout=60, env=self.world.environ(**extra), stdin=subprocess.DEVNULL)

    def test_status_json_exit_codes(self):
        result = self.run_cli("status", "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"], "undetermined")
        self.world.cli("codex", "plugin list --json", codex_plugins(skip=("ponytail",)))
        result = self.run_cli("status", "--json")
        self.assertEqual((result.returncode, json.loads(result.stdout)["result"]), (1, "not-ready"))
        self.assertFalse(self.world.state.exists())

    def test_status_prints_a_plain_table_by_default(self):
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Workspace readiness: undetermined", result.stdout)
        self.assertRegex(result.stdout, r"Superpowers\s+ready\s+ready\s+ready")
        self.assertIn("Details:", result.stdout)
        self.assertNotIn(str(self.world.home), result.stdout)

    def test_client_argument_marks_the_session_client(self):
        result = self.run_cli("status", "--json", "--client", "claude-code")
        self.assertEqual(json.loads(result.stdout)["session_client"], "claude-code")
        self.assertEqual(self.run_cli("status", "--client", "other").returncode, 2)

    def test_record_writes_once_and_exits_zero(self):
        result = self.run_cli("record")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["recorded"])
        self.assertEqual(ws.read_record(ws.record_path(self.world.environ()))["result"], "undetermined")

    def test_record_exits_two_when_it_cannot_write(self):
        self.world.state.parent.mkdir(exist_ok=True)
        self.world.state.write_text("a file, not a directory")
        result = self.run_cli("record")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["recorded"])

    def test_unusable_requirements_or_bad_arguments_exit_two_not_one(self):
        with tempfile.TemporaryDirectory(prefix="vibe broken package ") as temp:
            package = Path(temp)
            (package / "scripts").mkdir()
            (package / "clients").mkdir()
            (package / "runtime").symlink_to(PACKAGE / "runtime")
            (package / "scripts/workflow.py").write_bytes((PACKAGE / "scripts/workflow.py").read_bytes())
            for text in ("{", json.dumps(dict(json.loads((PACKAGE / "clients/workspace-requirements.json").read_text()), toolkit_revision="7ef68dd"))):
                (package / "clients/workspace-requirements.json").write_text(text)
                for action in ("status", "record"):
                    result = subprocess.run([sys.executable, "-B", str(package / "scripts/workflow.py"), "workspace", action, "--json"],
                                            capture_output=True, text=True, timeout=60, env=self.world.environ(), stdin=subprocess.DEVNULL)
                    self.assertEqual(result.returncode, 2, (text[:20], action, result.stdout, result.stderr))
                    self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertFalse(self.world.state.exists())
        for args in (("bogus",), ("status", "--unknown-flag"), ()):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertFalse(json.loads(result.stdout)["ok"])

    def test_workspace_is_a_control_command_that_needs_no_enrollment(self):
        result = self.run_cli("status", "--json")
        self.assertNotIn("not enrolled", result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
