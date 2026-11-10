"""Upgrade drift tests: observed client installs, the expected-version record and the startup warning.

No real client, network or home directory: installs are fixture text or fake executables on PATH,
and the record lives in a temporary XDG state directory."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime import package_versions as pv
from tests import test_startup


PACKAGE = Path(__file__).resolve().parents[1]
VERSION = json.loads((PACKAGE / ".codex-plugin/plugin.json").read_text())["version"]

def codex_entry(marketplace="vibe-workflow-local", version=VERSION, enabled=True, source=None, installed=True, name="vibe-workflow"):
    entry = {"pluginId": f"{name}@{marketplace}", "name": name, "marketplaceName": marketplace, "version": version,
             "installed": installed, "enabled": enabled, "installPolicy": "AVAILABLE", "authPolicy": "ON_INSTALL"}
    entry["source"] = {"source": "local", "path": "/Users/example/dev/vibe-workflow"} if source is None else source
    return entry


def claude_entry(marketplace="vibe-workflow-local", version=VERSION, enabled=True, source="/Users/example/dev/vibe-workflow", name="vibe-workflow"):
    return {"id": f"{name}@{marketplace}", "version": version, "scope": "user", "enabled": enabled,
            "installPath": f"/Users/example/.claude/plugins/cache/{marketplace}/{name}/{version}",
            "readFromFolder": source, "folderVersion": version, "installedAt": "2026-11-08T14:30:39.890Z",
            "lastUpdated": "2026-11-08T18:43:03.876Z", "projectEnabled": False}


OTHER_CODEX = [codex_entry("openai-primary-runtime", "26.1104.22356", name="documents",
                           source={"source": "local", "path": "/Users/example/plugins/documents"}),
               # An entry without marketplace metadata or with a Git source, as the real listing has both.
               codex_entry("ponytail", "5.3.3", name="ponytail", source={"source": "git", "url": "https://example.invalid/p.git", "ref": "main"})]
OTHER_CLAUDE = [claude_entry("caveman", "4.1.0", name="caveman", source=None)]


def codex_with(*entries):
    return json.dumps({"installed": [*OTHER_CODEX, *entries], "available": []})


def claude_with(*entries):
    return json.dumps([*OTHER_CLAUDE, *entries])


def codex_with_version(version):
    return codex_with(codex_entry(version=version))


def claude_with_version(version):
    return claude_with(claude_entry(version=version))


CODEX_OUTPUT = codex_with(codex_entry())
CLAUDE_OUTPUT = claude_with(claude_entry())


class FakeRun:
    """Stands in for subprocess.run so no client executable is spawned."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, argv, **options):
        self.calls.append((list(argv), options))
        value = self.outputs[argv[0]]
        if isinstance(value, BaseException):
            raise value
        code, text = value if isinstance(value, tuple) else (0, value)
        return subprocess.CompletedProcess(argv, code, stdout=text.encode(), stderr=b"")


class ParsingTests(unittest.TestCase):
    def test_codex_installed_entry_is_read_from_the_json_listing(self):
        parsed = pv.parse_codex(CODEX_OUTPUT)
        self.assertEqual(parsed["state"], "installed")
        self.assertEqual(parsed["installs"], [dict(marketplace="vibe-workflow-local", version=VERSION,
            source="/Users/example/dev/vibe-workflow", status="enabled")])

    def test_codex_git_source_and_missing_marketplace_metadata_are_accepted(self):
        git = codex_entry("git-release", "0.1.40", source={"source": "git", "url": "https://example.invalid/vibe.git", "ref": "main"})
        parsed = pv.parse_codex(codex_with(git))
        self.assertEqual(parsed["installs"][0]["source"], "https://example.invalid/vibe.git")

    def test_codex_uninstalled_row_and_lookalike_names_are_not_installs(self):
        text = codex_with(codex_entry("git-release", "0.1.40", installed=False),
                          codex_entry(name="vibe-workflow-extras", version="9.9.9"), codex_entry(name="vibecode"))
        parsed = pv.parse_codex(text)
        self.assertEqual((parsed["state"], parsed["installs"]), ("not-installed", []))
        self.assertEqual(pv.parse_codex('{"installed": []}'), dict(state="not-installed", installs=[]))

    def test_codex_reports_every_installed_marketplace_and_disabled_state(self):
        parsed = pv.parse_codex(codex_with(codex_entry("git-release", "0.1.2"), codex_entry("local", "0.1.33", enabled=False)))
        self.assertEqual([(i["marketplace"], i["version"], i["status"]) for i in parsed["installs"]],
                         [("git-release", "0.1.2", "enabled"), ("local", "0.1.33", "disabled")])

    def test_codex_unexpected_output_is_unknown_never_not_installed(self):
        entry = codex_entry()
        broken = ["", "   ", "garbled \x00 text", "PLUGIN  STATUS  VERSION  SOURCE\n", "null", "[]", '"installed"', "{}",
                  '{"installed": null}', '{"installed": {}}', '{"installed": ["text"]}', '{"installed": [{"name": "x"}]}',
                  '{"installed": [{"pluginId": 5}]}', "{" * 5000,
                  json.dumps({"installed": [dict(entry, version=None)]}), json.dumps({"installed": [dict(entry, version=33)]}),
                  json.dumps({"installed": [dict(entry, version="latest")]}), json.dumps({"installed": [dict(entry, installed="yes")]}),
                  json.dumps({"installed": [dict(entry, enabled=None)]}), json.dumps({"installed": [dict(entry, source=7)]}),
                  json.dumps({"installed": [{k: v for k, v in entry.items() if k != "version"}]})]
        for text in broken:
            with self.subTest(text=text[:60]):
                self.assertEqual(pv.parse_codex(text), dict(state="unknown", installs=[]))

    def test_claude_installed_entry_is_read_from_the_json_listing(self):
        parsed = pv.parse_claude(CLAUDE_OUTPUT)
        self.assertEqual(parsed["state"], "installed")
        self.assertEqual(parsed["installs"], [dict(marketplace="vibe-workflow-local", version=VERSION,
            source="/Users/example/dev/vibe-workflow", status="enabled")])

    def test_claude_entry_without_source_has_no_source_and_disabled_is_reported(self):
        parsed = pv.parse_claude(claude_with(claude_entry("git-release", "0.1.20", enabled=False, source=None)))
        self.assertEqual((parsed["installs"][0]["source"], parsed["installs"][0]["status"]), (None, "disabled"))

    def test_claude_absent_entry_is_not_installed(self):
        self.assertEqual(pv.parse_claude(claude_with()), dict(state="not-installed", installs=[]))
        self.assertEqual(pv.parse_claude("[]"), dict(state="not-installed", installs=[]))
        self.assertEqual(pv.parse_claude(claude_with(claude_entry(name="vibe-workflow-extras"))),
                         dict(state="not-installed", installs=[]))

    def test_claude_unexpected_output_is_unknown_never_not_installed(self):
        entry = claude_entry()
        broken = ["", "oops\n", "Installed plugins:\n", "{}", '{"installed": []}', "null", '["text"]', '[{"version": "1.0.0"}]',
                  '[{"id": 5}]', "[" * 5000, json.dumps([dict(entry, version=None)]), json.dumps([dict(entry, version=0.1)]),
                  json.dumps([dict(entry, version="not a version")]), json.dumps([dict(entry, enabled="true")]),
                  json.dumps([dict(entry, readFromFolder=3)]), json.dumps([{k: v for k, v in entry.items() if k != "version"}])]
        for text in broken:
            with self.subTest(text=text[:60]):
                self.assertEqual(pv.parse_claude(text), dict(state="unknown", installs=[]))

    def test_claude_other_entries_do_not_leak_into_the_install(self):
        parsed = pv.parse_claude(claude_with(claude_entry(), claude_entry("other", "7.0.0", name="other", source="/elsewhere")))
        self.assertEqual([i["version"] for i in parsed["installs"]], [VERSION])


class ObservationTests(unittest.TestCase):
    def observe(self, client, value):
        run = FakeRun({"codex": value, "claude": value})
        return pv.observe(client, run=run), run

    def test_official_cli_is_called_with_a_timeout_and_no_input(self):
        result, run = self.observe("codex", CODEX_OUTPUT)
        argv, options = run.calls[0]
        self.assertEqual(argv, ["codex", "plugin", "list", "--json"])
        self.assertLessEqual(options["timeout"], 15)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(result["state"], "installed")
        result, run = self.observe("claude-code", CLAUDE_OUTPUT)
        self.assertEqual(run.calls[0][0], ["claude", "plugin", "list", "--json"])
        self.assertEqual(result["state"], "installed")

    def test_missing_cli_timeout_failure_and_garbage_are_unknown(self):
        cases = {"cli-missing": FileNotFoundError(), "timeout": subprocess.TimeoutExpired("codex", 1),
                 "cli-failed": (2, "boom"), "unrecognized-output": (0, "nonsense")}
        for reason, value in cases.items():
            with self.subTest(reason=reason):
                result, _ = self.observe("codex", value)
                self.assertEqual((result["state"], result["reason"], result["installs"]), ("unknown", reason, []))

    def test_oversized_output_is_unknown(self):
        result, _ = self.observe("claude-code", "[" + " " * pv.MAX_OUTPUT + "]")
        self.assertEqual((result["state"], result["reason"]), ("unknown", "unrecognized-output"))

    def test_os_error_while_starting_is_unknown(self):
        result, _ = self.observe("codex", PermissionError())
        self.assertEqual((result["state"], result["reason"]), ("unknown", "cli-failed"))


class StateDirectoryMixin:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe versions ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.state = self.base / "state"
        self.home = self.base / "home"
        self.environ = dict(XDG_STATE_HOME=str(self.state), HOME=str(self.home))
        self.path = pv.record_path(self.environ)


class RecordPathTests(StateDirectoryMixin, unittest.TestCase):
    def test_xdg_state_home_wins_and_default_is_under_home(self):
        self.assertEqual(self.path, self.state / "vibe-workflow" / "package-release.json")
        self.assertEqual(pv.record_path(dict(HOME="/home/u")),
                         Path("/home/u/.local/state/vibe-workflow/package-release.json"))
        self.assertEqual(pv.record_path(dict(HOME="/home/u", XDG_STATE_HOME="relative")),
                         Path("/home/u/.local/state/vibe-workflow/package-release.json"))


class RecordFileTests(StateDirectoryMixin, unittest.TestCase):
    def test_write_is_private_atomic_and_readable(self):
        pv.write_record(self.path, "0.1.33", {"codex": "0.1.33"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(pv.read_record(self.path)["version"], "0.1.33")
        self.assertEqual(os.listdir(self.path.parent), [self.path.name])
        pv.write_record(self.path, "0.1.34", {})
        self.assertEqual(pv.read_record(self.path)["version"], "0.1.34")
        self.assertEqual(os.listdir(self.path.parent), [self.path.name])

    def test_existing_shared_state_directory_keeps_its_mode(self):
        self.path.parent.mkdir(parents=True)
        os.chmod(self.path.parent, 0o755)
        pv.write_record(self.path, "0.1.33", {})
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_symlinked_or_non_directory_state_directory_is_refused(self):
        target = self.base / "elsewhere"
        target.mkdir()
        self.path.parent.parent.mkdir(parents=True)
        self.path.parent.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            pv.write_record(self.path, "0.1.33", {})
        self.assertEqual(os.listdir(target), [])
        self.path.parent.unlink()
        self.path.parent.write_text("file")
        with self.assertRaises(OSError):
            pv.write_record(self.path, "0.1.33", {})

    def test_directory_owned_by_someone_else_is_refused(self):
        self.path.parent.mkdir(parents=True)
        with patch("runtime.package_versions.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(OSError):
                pv.write_record(self.path, "0.1.33", {})
        self.assertEqual(os.listdir(self.path.parent), [])

    def test_failed_replace_keeps_the_previous_record_and_leaves_no_temporary_file(self):
        pv.write_record(self.path, "0.1.33", {})
        before = self.path.read_bytes()
        with patch("runtime.package_versions.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                pv.write_record(self.path, "0.1.34", {})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(os.listdir(self.path.parent), [self.path.name])

    def test_absent_or_invalid_records_read_as_none(self):
        self.assertIsNone(pv.read_record(self.path))
        self.path.parent.mkdir(parents=True)
        valid = dict(schema_version=1, package="vibe-workflow", version="0.1.33", recorded_at="2026-11-08T00:00:00Z", clients={})
        bad = {"not json": b"{", "not an object": b"[1]", "wrong schema": json.dumps(dict(valid, schema_version=2)).encode(),
               "wrong package": json.dumps(dict(valid, package="other")).encode(),
               "bad version": json.dumps(dict(valid, version="latest")).encode(),
               "no version": json.dumps({k: v for k, v in valid.items() if k != "version"}).encode(),
               "oversized": json.dumps(dict(valid, padding="x" * pv.MAX_RECORD)).encode(),
               "binary": b"\xff\xfe\x00", "empty": b""}
        for name, raw in bad.items():
            with self.subTest(name=name):
                self.path.write_bytes(raw)
                self.assertIsNone(pv.read_record(self.path))
        self.path.write_bytes(json.dumps(valid).encode())
        self.assertEqual(pv.read_record(self.path)["version"], "0.1.33")

    def test_symlink_and_directory_records_are_not_read(self):
        self.path.parent.mkdir(parents=True)
        target = self.base / "elsewhere.json"
        pv.write_record(target, "0.1.33", {})
        self.path.symlink_to(target)
        self.assertIsNone(pv.read_record(self.path))
        self.path.unlink()
        self.path.mkdir()
        self.assertIsNone(pv.read_record(self.path))


class PackageVersionTests(unittest.TestCase):
    def test_versions_come_from_the_client_manifests(self):
        self.assertEqual(pv.package_version(PACKAGE), VERSION)
        self.assertEqual(pv.manifest_version(PACKAGE, "codex"), VERSION)
        self.assertEqual(pv.manifest_version(PACKAGE, "claude-code"), VERSION)
        self.assertIsNone(pv.manifest_version(PACKAGE, "other"))
        self.assertIsNone(pv.manifest_version(PACKAGE / "missing", "codex"))


class StatusAndRecordTests(StateDirectoryMixin, unittest.TestCase):
    def run_status(self, codex=None, claude=None):
        run = FakeRun({"codex": codex if codex is not None else CODEX_OUTPUT,
                       "claude": claude if claude is not None else CLAUDE_OUTPUT})
        return pv.status(PACKAGE, self.environ, run=run)

    def run_record(self, codex=None, claude=None):
        run = FakeRun({"codex": codex if codex is not None else CODEX_OUTPUT,
                       "claude": claude if claude is not None else CLAUDE_OUTPUT})
        return pv.record(PACKAGE, self.environ, run=run)

    def test_status_reports_package_record_and_each_client_without_writing(self):
        report = self.run_status()
        self.assertEqual(report["package_version"], VERSION)
        self.assertEqual(report["record"], dict(state="absent"))
        self.assertEqual({client["client"]: client["state"] for client in report["clients"]},
                         {"codex": "installed", "claude-code": "installed"})
        self.assertTrue(all(client["matches"] for client in report["clients"]))
        self.assertFalse(self.state.exists())
        self.assertFalse(self.home.exists())

    def test_status_shows_a_valid_record_and_flags_a_differing_client(self):
        pv.write_record(self.path, "9.9.9", {})
        before = self.path.read_bytes()
        report = self.run_status(codex=codex_with_version("0.1.0"))
        self.assertEqual((report["record"]["state"], report["record"]["version"]), ("valid", "9.9.9"))
        by_client = {client["client"]: client for client in report["clients"]}
        self.assertFalse(by_client["codex"]["matches"])
        self.assertTrue(by_client["claude-code"]["matches"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_status_marks_an_invalid_record_and_unknown_clients(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{")
        report = self.run_status(codex="garbage", claude=FileNotFoundError())
        self.assertEqual(report["record"], dict(state="invalid"))
        by_client = {client["client"]: client for client in report["clients"]}
        self.assertEqual(by_client["codex"]["state"], "unknown")
        self.assertEqual(by_client["claude-code"]["reason"], "cli-missing")
        self.assertIsNone(by_client["claude-code"]["matches"])

    def test_record_refuses_while_an_installed_client_reports_another_version(self):
        result = self.run_record(codex=codex_with_version("0.1.0"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["recorded"])
        self.assertIn("codex", result["error"])
        self.assertFalse(self.path.exists())

    def test_record_refuses_when_any_marketplace_install_differs(self):
        text = codex_with(codex_entry(), codex_entry("other", "0.0.1"))
        self.assertFalse(self.run_record(codex=text)["ok"])
        self.assertFalse(self.path.exists())

    def test_record_refuses_when_an_installed_client_cannot_be_read(self):
        for broken in ("garbage", subprocess.TimeoutExpired("codex", 1), (1, "failed")):
            with self.subTest(broken=repr(broken)):
                result = self.run_record(codex=broken)
                self.assertFalse(result["ok"])
                self.assertFalse(self.path.exists())

    def test_record_succeeds_when_every_installed_client_matches(self):
        result = self.run_record()
        self.assertTrue(result["ok"])
        self.assertTrue(result["recorded"])
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(pv.read_record(self.path)["version"], VERSION)
        self.assertEqual(set(pv.read_record(self.path)["clients"]), {"codex", "claude-code"})

    def test_client_that_is_not_installed_is_listed_and_does_not_block(self):
        absent = codex_with()
        result = self.run_record(codex=absent)
        self.assertTrue(result["ok"])
        self.assertEqual(result["not_installed"], ["codex"])
        self.assertEqual(list(pv.read_record(self.path)["clients"]), ["claude-code"])

    def test_missing_cli_refuses_and_names_the_client_and_reason(self):
        result = self.run_record(claude=FileNotFoundError())
        self.assertFalse(result["ok"])
        self.assertEqual(result["not_installed"], [])
        self.assertIn("claude-code", result["error"])
        self.assertIn("cli-missing", result["error"])
        self.assertIn("not upgraded", result["error"])
        self.assertFalse(self.path.exists())

    def test_every_unknown_reason_refuses_and_is_never_reported_as_not_installed(self):
        for value, reason in ((FileNotFoundError(), "cli-missing"), (subprocess.TimeoutExpired("codex", 1), "timeout"),
                              ((1, "failed"), "cli-failed"), ("garbage", "unrecognized-output"), ('{"installed": null}', "unrecognized-output")):
            with self.subTest(reason=reason):
                result = self.run_record(codex=value)
                self.assertFalse(result["ok"])
                self.assertEqual(result["not_installed"], [])
                self.assertIn(f"codex could not be read ({reason})", result["error"])
                self.assertFalse(self.path.exists())

    def test_record_refuses_when_no_client_is_confirmed_at_the_package_version(self):
        result = self.run_record(codex=codex_with(), claude=claude_with())
        self.assertEqual(result["not_installed"], ["codex", "claude-code"])
        self.assertFalse(result["ok"])
        self.assertFalse(self.path.exists())

    def test_refusal_keeps_the_existing_record(self):
        pv.write_record(self.path, "0.0.1", {})
        before = self.path.read_bytes()
        self.assertFalse(self.run_record(claude=claude_with_version("0.0.9"))["ok"])
        self.assertEqual(self.path.read_bytes(), before)


class DriftWarningTests(StateDirectoryMixin, unittest.TestCase):
    def warn(self, client="codex", package=PACKAGE):
        return pv.drift_warning(package, client, self.environ)

    def test_no_record_matching_record_and_corrupt_record_are_silent(self):
        self.assertIsNone(self.warn())
        pv.write_record(self.path, VERSION, {})
        self.assertIsNone(self.warn())
        self.path.write_text("{")
        self.assertIsNone(self.warn())

    def test_difference_names_client_versions_and_next_action(self):
        pv.write_record(self.path, "9.9.9", {})
        for client in ("codex", "claude-code"):
            with self.subTest(client=client):
                message = self.warn(client)
                for expected in (client, VERSION, "9.9.9", "prepare-my-workspace upgrade"):
                    self.assertIn(expected, message)

    def test_unreadable_manifest_or_unknown_client_is_silent(self):
        pv.write_record(self.path, "9.9.9", {})
        self.assertIsNone(self.warn("other"))
        self.assertIsNone(self.warn(package=self.base / "missing"))

    def test_comparison_does_no_subprocess_network_or_write(self):
        pv.write_record(self.path, "9.9.9", {})
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns, sorted(os.listdir(self.path.parent)))
        forbidden = AssertionError("startup must not spawn, connect or write")
        with patch("subprocess.run", side_effect=forbidden), patch("subprocess.Popen", side_effect=forbidden), \
                patch("socket.socket", side_effect=forbidden), patch("os.replace", side_effect=forbidden), \
                patch("tempfile.mkstemp", side_effect=forbidden):
            self.assertIsNotNone(self.warn())
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns, sorted(os.listdir(self.path.parent))))

    def test_append_warning_extends_both_fields_and_copies_the_output(self):
        output = {"systemMessage": "Existing.", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Context."}}
        original = json.dumps(output)
        merged = pv.append_warning(output, "Drift.")
        self.assertEqual(json.dumps(output), original)
        self.assertEqual(merged["systemMessage"], "Existing. Drift.")
        self.assertEqual(merged["hookSpecificOutput"]["additionalContext"], "Context. Drift.")
        bare = pv.append_warning({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "C."}}, "D.")
        self.assertEqual(bare["systemMessage"], "D.")
        self.assertEqual(pv.append_warning({"other": 1}, "D."), {"other": 1})


class FakeClients:
    """Fake `codex` and `claude` executables; each records that it ran."""

    def __init__(self, base, codex=CODEX_OUTPUT, claude=CLAUDE_OUTPUT):
        self.bin = base / "bin"
        self.bin.mkdir()
        self.marker = base / "cli-ran"
        for name, text in (("codex", codex), ("claude", claude)):
            output = base / f"{name}.txt"
            output.write_text(text)
            script = self.bin / name
            script.write_text(f"#!/bin/sh\necho {name} >> '{self.marker}'\ncat '{output}'\n")
            script.chmod(0o755)

    def environment(self, extra):
        return {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", **extra}


class ControlTests(StateDirectoryMixin, unittest.TestCase):
    def control(self, action, clients, extra_args=()):
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"), "upgrade", action, *extra_args],
                                capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                                env=clients.environment(self.environ), cwd=self.base)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_status_reads_installs_through_the_clis_and_writes_nothing(self):
        clients = FakeClients(self.base)
        code, report = self.control("status", clients)
        self.assertEqual(code, 0)
        self.assertEqual(report["package_version"], VERSION)
        self.assertEqual(report["record"], dict(state="absent"))
        self.assertEqual({c["client"]: c["state"] for c in report["clients"]}, {"codex": "installed", "claude-code": "installed"})
        self.assertEqual(clients.marker.read_text().split(), ["codex", "claude"])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.home.exists())

    def test_record_refuses_then_succeeds_once_versions_match(self):
        stale = FakeClients(self.base, codex=codex_with_version("0.1.0"))
        code, report = self.control("record", stale)
        self.assertEqual((code, report["ok"], report["recorded"]), (1, False, False))
        self.assertFalse(self.path.exists())
        shutil.rmtree(stale.bin)
        current = FakeClients(self.base)
        code, report = self.control("record", current)
        self.assertEqual((code, report["ok"], report["recorded"]), (0, True, True))
        self.assertEqual(pv.read_record(self.path)["version"], VERSION)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_missing_client_clis_are_unknown_not_a_guess(self):
        empty = self.base / "empty-bin"
        empty.mkdir()
        python = Path(sys.executable).parent
        environment = {**os.environ, "PATH": f"{empty}{os.pathsep}{python}", **self.environ}
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"), "upgrade", "status"],
                                capture_output=True, text=True, timeout=30, env=environment, cwd=self.base)
        report = json.loads(result.stdout)
        if shutil.which("codex", path=str(empty)) or shutil.which("claude", path=str(empty)):
            self.skipTest("client CLI found beside the interpreter")
        self.assertEqual({c["client"]: (c["state"], c["reason"]) for c in report["clients"]},
                         {"codex": ("unknown", "cli-missing"), "claude-code": ("unknown", "cli-missing")})

    def test_unexpected_arguments_are_rejected_as_json(self):
        clients = FakeClients(self.base)
        code, report = self.control("status", clients, ("--cwd", "/tmp"))
        self.assertEqual((code, report["ok"]), (1, False))


class StartupWarningTests(unittest.TestCase):
    client = "codex"
    git = test_startup.StartupTests.git
    enroll = test_startup.StartupTests.enroll
    invoke = test_startup.StartupTests.invoke

    def setUp(self):
        test_startup.StartupTests.setUp(self)
        self.state = self.base / "state"
        self.path = pv.record_path(dict(XDG_STATE_HOME=str(self.state), HOME=str(self.base / "home")))
        self.clients = FakeClients(self.base)
        self.environment = self.clients.environment(dict(XDG_STATE_HOME=str(self.state), HOME=str(self.base / "home")))
        self.copied = None

    def hook(self, package=PACKAGE):
        return self.invoke(package=package, env=self.environment)

    def stale_package(self):
        package = self.base / "stale plugin"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for directory in (".codex-plugin", ".claude-plugin"):
            manifest = package / directory / "plugin.json"
            manifest.write_text(manifest.read_text().replace(f'"version": "{VERSION}"', '"version": "0.1.0"'))
        return package

    @staticmethod
    def as_package(text, package):
        return text.replace("'", "").replace(str(package), str(PACKAGE))

    def test_record_newer_than_the_client_manifest_adds_one_warning(self):
        self.enroll()
        plain = self.hook()
        package = self.stale_package()
        pv.write_record(self.path, "0.1.33", {})
        output = self.hook(package)
        context = output["hookSpecificOutput"]["additionalContext"]
        for text in (self.client, "0.1.0", "0.1.33", "prepare-my-workspace upgrade"):
            self.assertIn(text, output["systemMessage"])
            self.assertIn(text, context)
        self.assertEqual(context.count("prepare-my-workspace upgrade"), 1)
        self.assertTrue(self.as_package(context, package).startswith(
            plain["hookSpecificOutput"]["additionalContext"].replace("'", "")))

    def test_matching_record_or_no_record_adds_no_warning(self):
        self.enroll()
        baseline = self.hook()
        self.assertEqual(set(baseline), {"hookSpecificOutput"})
        pv.write_record(self.path, VERSION, {})
        self.assertEqual(self.hook(), baseline)

    def test_corrupt_record_leaves_the_startup_result_unchanged(self):
        self.enroll()
        baseline = self.hook()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for raw in (b"{", b"\xff", b"x" * (pv.MAX_RECORD + 10)):
            self.path.write_bytes(raw)
            with self.subTest(raw=raw[:4]):
                self.assertEqual(self.hook(), baseline)

    def test_unenrolled_checkout_stays_silent_even_with_drift(self):
        pv.write_record(self.path, "9.9.9", {})
        self.assertIsNone(self.hook())

    def test_startup_writes_only_the_delivery_note_and_never_spawns_a_client(self):
        self.enroll()
        pv.write_record(self.path, "9.9.9", {})
        before = [(str(p), p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(self.state.rglob("*")) if p.is_file()]
        output = self.hook()
        self.assertIn("9.9.9", output["systemMessage"])
        self.assertFalse(self.clients.marker.exists())
        after = [(str(p), p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(self.state.rglob("*")) if p.is_file()]
        self.assertEqual(before, [entry for entry in after if "/delivered/" not in entry[0]])
        self.assertEqual([Path(entry[0]).name for entry in after if "/delivered/" in entry[0]], [f"{self.client}.json"])
        self.assertFalse((self.base / "home").exists())

    def test_failure_while_checking_leaves_the_output_unchanged(self):
        self.enroll()
        package = self.base / "broken plugin"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        original = self.hook(package)
        pv.write_record(self.path, "9.9.9", {})
        self.assertIn("9.9.9", self.hook(package)["systemMessage"])
        (package / "runtime/package_versions.py").write_text("raise RuntimeError('broken')\n")
        self.assertEqual(self.hook(package), original)


class ClaudeStartupWarningTests(StartupWarningTests):
    client = "claude-code"
    other_client = "codex"


if __name__ == "__main__":
    unittest.main()
