"""Read-only observation for workspace readiness: bounded CLI runs, listing parsers and config reads.

A client or tool is observed only through its own CLI listing or a config file it documents. Nothing
here writes, installs or starts a client, and nothing adds network use of its own; the one check that
may contact the network is the GitHub sign-in (`gh auth status`) run by the caller. MCP servers are
read from configuration only, as names and flags, never values. Unreadable or unexpected output is
`None` from a parser, which callers report as `unknown`, never as absent."""

import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import time
import tomllib

from runtime import package_versions as pv


CLI_TIMEOUT = pv.CLI_TIMEOUT
MAX_OUTPUT = 4 * 1024 * 1024
MAX_CONFIG = 2 * 1024 * 1024
MAX_CLIENT_STATE = 32 * 1024 * 1024
CHUNK = 64 * 1024
VERSION_TEXT = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-[0-9A-Za-z.-]+|(?:a|b|rc|dev)[0-9]+|\.post[0-9]+)?(?:\+[0-9A-Za-z.-]+)?")
CLIENT_COMMANDS = {"codex": "codex", "claude-code": "claude", "opencode": "opencode"}
SESSION_MARKERS = {"codex": "CODEX_THREAD_ID", "claude-code": "CLAUDECODE", "opencode": "OPENCODE"}
LISTINGS = {("codex", "plugin"): ("codex", "plugin", "list", "--json"),
            ("claude-code", "plugin"): ("claude", "plugin", "list", "--json"),
            ("opencode", "plugin"): ("opencode", "plugin", "list"),
            ("codex", "mcp"): ("codex", "mcp", "list", "--json")}


class Observer:
    """Runs each distinct command at most once inside one overall time budget."""

    def __init__(self, environ, budget):
        self.environ = dict(environ)
        self.deadline = time.monotonic() + budget
        self.results = {}

    def which(self, command):
        return shutil.which(command, path=self.environ.get("PATH", os.defpath))

    def run(self, argv):
        """`dict(code, out, stdout)` once the command ran, or `dict(reason)` when it could not be observed."""
        key = tuple(argv)
        if key not in self.results:
            self.results[key] = self._run(list(argv))
        return self.results[key]

    def _run(self, argv):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return dict(reason="time-budget")
        timeout = min(CLI_TIMEOUT, remaining)
        try:
            # Own process group, so stopping the command also ends children that would keep its pipes open.
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                       env=self.environ, start_new_session=True)
        except FileNotFoundError:
            return dict(reason="cli-missing")
        except (OSError, ValueError):
            return dict(reason="cli-failed")
        outcome, stdout, stderr = _collect(process, timeout)
        if outcome == "timeout":
            return dict(reason="time-budget" if timeout < CLI_TIMEOUT else "timeout")
        if outcome == "output-too-large":
            return dict(reason=outcome)
        return dict(code=process.returncode, out=(stdout + stderr).decode("utf-8", "replace"), stdout=stdout.decode("utf-8", "replace"))


def _stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()
    for stream in (process.stdout, process.stderr):
        stream.close()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _collect(process, timeout):
    """Read both pipes in chunks. Stops the process group at the time limit or once the output passes
    MAX_OUTPUT: `(outcome, stdout, stderr)` with outcome `ok`, `timeout` or `output-too-large`."""
    deadline = time.monotonic() + timeout
    data = {process.stdout: bytearray(), process.stderr: bytearray()}
    total = 0
    with selectors.DefaultSelector() as selector:
        for stream in data:
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop(process)
                return "timeout", b"", b""
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, CHUNK)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                data[key.fileobj] += chunk
                total += len(chunk)
                if total > MAX_OUTPUT:
                    _stop(process)
                    return "output-too-large", b"", b""
    try:
        process.wait(timeout=max(deadline - time.monotonic(), 0.001))
    except subprocess.TimeoutExpired:
        _stop(process)
        return "timeout", b"", b""
    process.stdout.close()
    process.stderr.close()
    return "ok", bytes(data[process.stdout]), bytes(data[process.stderr])


def listing(observer, client, kind):
    """The stdout of a client's own plugin or MCP listing; `(text, None)` or `(None, reason)`."""
    result = observer.run(LISTINGS[(client, kind)])
    if "reason" in result:
        return None, result["reason"]
    if result["code"] != 0:
        return None, "cli-failed"
    return result["stdout"], None


def cli_version(observer, command):
    """`(version, None)` for a working command, or `(None, reason)`."""
    result = observer.run((command, "--version"))
    if "reason" in result:
        return None, result["reason"]
    match = VERSION_TEXT.search(result["out"]) if result["code"] == 0 else None
    return (match.group(0), None) if match else (None, "cli-failed" if result["code"] else "unrecognized-output")


def session_client(environ, requested=None):
    """The client running this session: the argument when given, else exactly one environment marker."""
    if requested is not None:
        return requested if requested in CLIENT_COMMANDS else None
    found = [client for client, marker in SESSION_MARKERS.items() if environ.get(marker)]
    return found[0] if len(found) == 1 else None


def home_dir(environ):
    return Path(environ.get("HOME") or Path.home())


def client_home(client, environ):
    home = home_dir(environ)
    if client == "codex":
        return Path(environ.get("CODEX_HOME") or home / ".codex")
    if client == "claude-code":
        return Path(environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    base = environ.get("XDG_CONFIG_HOME")
    return Path(base if base and os.path.isabs(base) else home / ".config") / "opencode"


def read_text(path, limit=None):
    """A regular file's text; FileNotFoundError when absent and ValueError when unusable."""
    limit = MAX_CONFIG if limit is None else limit
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError:
        raise ValueError("Unreadable file") from None
    try:
        if not os.fstat(descriptor).st_mode & 0o170000 == 0o100000:
            raise ValueError("Expected a regular file")
        data = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise ValueError("File is too large")
    return data.decode("utf-8")


def read_json(path, limit=None):
    try:
        return json.loads(read_text(path, limit))
    except (UnicodeDecodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Unreadable JSON") from error


def strip_jsonc(text):
    """Comments and trailing commas removed from JSONC, leaving string contents alone."""
    out, index, size = [], 0, len(text)
    while index < size:
        char = text[index]
        if char == '"':
            end = index + 1
            while end < size and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[index:end + 1])
            index = end + 1
        elif text.startswith("//", index):
            index = text.find("\n", index) if "\n" in text[index:] else size
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("Unterminated comment")
            index = end + 2
        else:
            out.append(char)
            index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def read_jsonc(path, limit=None):
    try:
        return json.loads(strip_jsonc(read_text(path, limit)))
    except (UnicodeDecodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Unreadable JSON") from error


def read_toml(path):
    try:
        return tomllib.loads(read_text(path))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("Unreadable TOML") from error


def spec_name(spec):
    """The plugin name inside a package spec or source: the last path segment with `git+`, `.git`,
    a query or fragment and an `@version` stripped, so `superpowers@git+https://x/y/superpowers.git`,
    `@scope/ponytail` and `ponytail@1.2.3` give `superpowers`, `ponytail` and `ponytail`."""
    text = spec.strip().lower()
    text = re.split(r"[#?]", text, maxsplit=1)[0].rstrip("/")
    text = text.removeprefix("git+").removesuffix(".git")
    segment = re.split(r"[/:]", text)[-1]
    return segment.partition("@")[0] if not segment.startswith("@") else segment[1:].partition("@")[0]


def _name(identifier):
    return identifier.partition("@")[0].lower()


def codex_plugins(text):
    """Installed entries of `codex plugin list --json` as `dict(names, enabled)`; None when unrecognized."""
    data = pv._load(text)
    if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
        return None
    entries = []
    for entry in data["installed"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("pluginId"), str) or not isinstance(entry.get("enabled"), bool):
            return None
        if entry.get("installed") is not False:
            entries.append(dict(names={_name(entry["pluginId"])}, enabled=entry["enabled"]))
    return entries


def claude_plugins(text):
    """Entries of `claude plugin list --json` as `dict(names, enabled, servers)`; None when unrecognized.
    `servers` are the names of MCP servers the plugin itself declares."""
    data = pv._load(text)
    if not isinstance(data, list):
        return None
    entries = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("enabled"), bool):
            return None
        servers = entry.get("mcpServers")
        entries.append(dict(names={_name(entry["id"])}, enabled=entry["enabled"],
                            servers=sorted(name.lower() for name in servers) if isinstance(servers, dict) else []))
    return entries


def opencode_plugins(text):
    """Rows of the `opencode plugin list` table as `dict(names, enabled)`: the names read from the ID and
    SOURCE columns. None unless the header is present, so error text is never taken for an empty list."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or [column.upper() for column in re.split(r"\s{2,}", lines[0])[:2]] != ["ID", "VERSION"]:
        return None
    entries = []
    for row in (re.split(r"\s{2,}", line) for line in lines[1:]):
        if len(row) < 2:
            return None
        names = {spec_name(value) for value in (row[0], *row[2:]) if value != "-"}
        entries.append(dict(names=names - {""}, enabled=True))
    return entries


def plugin_state(entries, names):
    """`(state, reason)` for a plugin among parsed entries. Every entry with the exact name must be
    enabled: a disabled one is not hidden by an enabled copy elsewhere."""
    found = [entry for entry in entries if entry["names"] & set(names)]
    if not found:
        return "missing", "not installed in this client"
    if not all(entry["enabled"] for entry in found):
        return "missing", "installed but disabled"
    return "ready", None


def codex_mcp(text):
    """Entries of `codex mcp list --json` as `dict(name, status)`; None when unrecognized."""
    data = pv._load(text)
    if not isinstance(data, list):
        return None
    entries = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("enabled"), bool):
            return None
        status = "disabled" if not entry["enabled"] else "needs-auth" if entry.get("auth_status") == "not_logged_in" else "ok"
        entries.append(dict(name=entry["name"].lower(), status=status))
    return entries


def _servers(table):
    """Server names and flags from an `mcpServers`/`mcp` object, without reading any value but the flags."""
    if table is None:
        return []
    if not isinstance(table, dict):
        raise ValueError("Unrecognized MCP configuration")
    entries = []
    for name, entry in table.items():
        if not isinstance(entry, dict):
            raise ValueError("Unrecognized MCP configuration")
        off = entry.get("enabled") is False or entry.get("disabled") is True
        entries.append(dict(name=str(name).lower(), status="disabled" if off else "ok"))
    return entries


def claude_mcp(environ, plugin_entries):
    """Claude Code servers from its user config files and from enabled plugins that declare them.
    Absent files mean none configured; an unreadable file raises ValueError."""
    folder = environ.get("CLAUDE_CONFIG_DIR")
    entries = []
    for path in (Path(folder) / ".claude.json" if folder else home_dir(environ) / ".claude.json",
                 client_home("claude-code", environ) / "settings.json"):
        try:
            data = read_json(path, MAX_CLIENT_STATE)
        except FileNotFoundError:
            continue
        if not isinstance(data, dict):
            raise ValueError("Unrecognized client configuration")
        entries += _servers(data.get("mcpServers"))
    for plugin in plugin_entries or []:
        entries += [dict(name=name, status="ok" if plugin["enabled"] else "disabled") for name in plugin.get("servers", [])]
    return entries


def _opencode_configs(environ):
    """The parsed `opencode.json` and `opencode.jsonc` that exist; an unreadable one raises ValueError."""
    configs = []
    for name in ("opencode.json", "opencode.jsonc"):
        try:
            data = read_jsonc(client_home("opencode", environ) / name)
        except FileNotFoundError:
            continue
        if not isinstance(data, dict):
            raise ValueError("Unrecognized client configuration")
        configs.append(data)
    return configs


def opencode_config_plugins(environ):
    """Plugins named in OpenCode's `plugin` and `plugins` lists as `dict(names, enabled)`. The
    `opencode plugin list` table omits local-path plugins such as `./plugins/caveman`."""
    entries = []
    for data in _opencode_configs(environ):
        for key in ("plugin", "plugins"):
            specs = data.get(key)
            if specs is None:
                continue
            if not isinstance(specs, list) or not all(isinstance(spec, str) for spec in specs):
                raise ValueError("Unrecognized client configuration")
            entries += [dict(names={spec_name(spec)} - {""}, enabled=True) for spec in specs]
    return entries


def opencode_mcp(environ):
    """OpenCode servers from the `mcp` key of its config: `mcp.<name>`, or `mcp.servers.<name>`
    as OpenCode 2.2.6 writes it."""
    entries = []
    for data in _opencode_configs(environ):
        table = data.get("mcp")
        if (isinstance(table, dict) and set(table) == {"servers"} and isinstance(table["servers"], dict)
                and all(isinstance(entry, dict) for entry in table["servers"].values())):
            table = table["servers"]
        entries += _servers(table)
    return entries


def mcp_state(entries, names):
    """`(state, reason)` for a server whose name matches exactly. Ready means configured and enabled;
    the connection is not checked. A disabled or signed-out entry is not hidden by a passing one."""
    found = [entry for entry in entries if entry["name"] in names]
    if not found:
        return "missing", "not configured in this client"
    statuses = {entry["status"] for entry in found}
    if "disabled" in statuses:
        return "missing", "configured but disabled"
    if "needs-auth" in statuses:
        return "needs-user", "configured but needs a sign-in"
    return "ready", None


RTK_LINE = re.compile(r"^\[(?P<mark>ok|--|!!)\] (?P<label>[^:]+):", re.MULTILINE)


def rtk_marks(text):
    """`{label: bool}` from `rtk init --show`; None when its header is absent or no row is recognized."""
    if not text.lstrip().startswith("rtk Configuration"):
        return None
    marks = {match["label"].strip(): match["mark"] == "ok" for match in RTK_LINE.finditer(text)}
    return marks or None
