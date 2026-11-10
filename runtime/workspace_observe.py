"""Read-only observation for workspace readiness: bounded CLI runs, listing parsers and config reads.

A client or tool is observed only through its own CLI listing or a config file it documents. Nothing
here writes, installs or uses the network on its own. Unreadable or unexpected output is `None`
from a parser, which callers report as `unknown`, never as absent."""

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import tomllib

from runtime import package_versions as pv


CLI_TIMEOUT = pv.CLI_TIMEOUT
MAX_OUTPUT = 4 * 1024 * 1024
MAX_CONFIG = 2 * 1024 * 1024
VERSION_TEXT = re.compile(r"\d+(?:\.\d+)+")
CLIENT_COMMANDS = {"codex": "codex", "claude-code": "claude", "opencode": "opencode"}
SESSION_MARKERS = {"codex": "CODEX_THREAD_ID", "claude-code": "CLAUDECODE", "opencode": "OPENCODE"}
LISTINGS = {("codex", "plugin"): ("codex", "plugin", "list", "--json"),
            ("claude-code", "plugin"): ("claude", "plugin", "list", "--json"),
            ("opencode", "plugin"): ("opencode", "plugin", "list"),
            ("codex", "mcp"): ("codex", "mcp", "list", "--json"),
            ("claude-code", "mcp"): ("claude", "mcp", "list"),
            ("opencode", "mcp"): ("opencode", "mcp", "list")}


class Observer:
    """Runs each distinct command at most once inside one overall time budget."""

    def __init__(self, environ, budget):
        self.environ = dict(environ)
        self.deadline = time.monotonic() + budget
        self.results = {}

    def which(self, command):
        return shutil.which(command, path=self.environ.get("PATH", os.defpath))

    def run(self, argv):
        """`dict(code, out)` once the command ran, or `dict(reason)` when it could not be observed."""
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
            # Own process group, so a timeout also ends children that would keep the output pipe open.
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                       env=self.environ, start_new_session=True)
        except FileNotFoundError:
            return dict(reason="cli-missing")
        except (OSError, ValueError):
            return dict(reason="cli-failed")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return dict(reason="time-budget" if timeout < CLI_TIMEOUT else "timeout")
        if len(stdout) + len(stderr) > MAX_OUTPUT:
            return dict(reason="unrecognized-output")
        return dict(code=process.returncode, out=(stdout + stderr).decode("utf-8", "replace"), stdout=stdout.decode("utf-8", "replace"))


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


def client_home(client, environ):
    home = environ.get("HOME") or str(Path.home())
    if client == "codex":
        return Path(environ.get("CODEX_HOME") or Path(home) / ".codex")
    if client == "claude-code":
        return Path(environ.get("CLAUDE_CONFIG_DIR") or Path(home) / ".claude")
    base = environ.get("XDG_CONFIG_HOME")
    return Path(base if base and os.path.isabs(base) else Path(home) / ".config") / "opencode"


def read_text(path, limit=MAX_CONFIG):
    """A regular file's text; FileNotFoundError when absent and ValueError when unusable."""
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


def read_json(path):
    try:
        return json.loads(read_text(path))
    except (UnicodeDecodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Unreadable JSON") from error


def read_toml(path):
    try:
        return tomllib.loads(read_text(path))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("Unreadable TOML") from error


def _load(text):
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _name(identifier):
    return identifier.partition("@")[0]


def codex_plugins(text):
    """Installed entries of `codex plugin list --json` as `dict(name, enabled)`; None when unrecognized."""
    data = _load(text)
    if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
        return None
    entries = []
    for entry in data["installed"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("pluginId"), str) or not isinstance(entry.get("enabled"), bool):
            return None
        if entry.get("installed") is not False:
            entries.append(dict(name=_name(entry["pluginId"]), enabled=entry["enabled"]))
    return entries


def claude_plugins(text):
    """Entries of `claude plugin list --json` as `dict(name, enabled)`; None when unrecognized."""
    data = _load(text)
    if not isinstance(data, list):
        return None
    entries = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("enabled"), bool):
            return None
        entries.append(dict(name=_name(entry["id"]), enabled=entry["enabled"]))
    return entries


def opencode_plugins(text):
    """Rows of the `opencode plugin list` table as `dict(haystack, enabled)`; None when unrecognized."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if lines[0].lower().startswith("no plugins"):
        return []
    header = re.split(r"\s{2,}", lines[0])
    if [column.upper() for column in header[:2]] != ["ID", "VERSION"]:
        return None
    rows = [re.split(r"\s{2,}", line) for line in lines[1:]]
    return [dict(haystack=" ".join(row[:1] + row[2:]).lower(), enabled=True) for row in rows]


def match_plugins(entries, names, exact):
    """Entries whose name equals (or, for listings without names, contains) one of the names."""
    if exact:
        return [entry for entry in entries if entry["name"].lower() in names]
    return [entry for entry in entries if any(name in entry["haystack"] for name in names)]


def codex_mcp(text):
    """Entries of `codex mcp list --json` as `dict(haystack, status)`; None when unrecognized."""
    data = _load(text)
    if not isinstance(data, list):
        return None
    entries = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("enabled"), bool):
            return None
        transport = entry.get("transport") if isinstance(entry.get("transport"), dict) else {}
        parts = [entry["name"], transport.get("url"), transport.get("command"), *(transport.get("args") or [])]
        status = "disabled" if not entry["enabled"] else "needs-auth" if entry.get("auth_status") == "not_logged_in" else "ok"
        entries.append(dict(haystack=" ".join(part for part in parts if isinstance(part, str)).lower(), status=status))
    return entries


MCP_LINE = re.compile(r"^(?P<name>.+?): (?P<target>.+) - (?P<status>\S.*)$")


def _mcp_status(text):
    text = text.lower()
    if "disabled" in text:
        return "disabled"
    if "authentication" in text or "needs auth" in text:
        return "needs-auth"
    if "failed" in text or "disconnected" in text or "not connected" in text or "error" in text:
        return "failed"
    return "ok" if "connected" in text else None


def claude_mcp(text):
    """Lines of `claude mcp list` as `dict(haystack, status)`; None when a line is not a server line."""
    entries = []
    for line in (line.strip() for line in text.splitlines()):
        if not line or line.startswith("Checking MCP server health"):
            continue
        if line.lower().startswith("no mcp servers configured"):
            return []
        match = MCP_LINE.match(line)
        status = _mcp_status(match["status"]) if match else None
        if status is None:
            return None
        entries.append(dict(haystack=(match["name"] + " " + match["target"]).lower(), status=status))
    return entries


def opencode_mcp(text):
    """Lines of `opencode mcp list` as `dict(haystack, status)`. Its populated layout is not pinned, so a
    line with no recognizable status keeps `status=None`."""
    if "no mcp servers configured" in text.lower():
        return []
    lines = [re.sub(r"[^\w.:/@ -]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return [dict(haystack=line.lower(), status=_mcp_status(line)) for line in lines] or None


def mcp_state(entries, names):
    """`(state, reason)` for a server among parsed MCP entries."""
    found = [entry for entry in entries if any(name in entry["haystack"] for name in names)]
    if not found:
        if entries and not any(entry["status"] for entry in entries):
            return "unknown", "unrecognized-output"
        return "missing", "not configured in this client"
    statuses = {entry["status"] for entry in found}
    if "ok" in statuses:
        return "ready", None
    if "needs-auth" in statuses:
        return "needs-user", "configured but needs a sign-in"
    if "disabled" in statuses:
        return "missing", "configured but disabled"
    return "unknown", "configured, but the client does not report it connected"


RTK_LINE = re.compile(r"^\[(?P<mark>ok|--|!!)\] (?P<label>[^:]+):", re.MULTILINE)


def rtk_marks(text):
    """`{label: bool}` from `rtk init --show`; None when its header is absent."""
    if not text.lstrip().startswith("rtk Configuration"):
        return None
    return {match["label"].strip(): match["mark"] == "ok" for match in RTK_LINE.finditer(text)}
