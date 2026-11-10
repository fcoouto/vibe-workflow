"""Workspace readiness: one state per requirement and client, and the last-result record.

`status` is read-only and bounded in time. It reads each client's own listings and documented config
files; it never installs, edits or starts anything. The only check that may use the network is the
GitHub sign-in (`gh auth status`). A state is `ready` only when its own evidence was seen; for an MCP
server that means configured, not connected. `record` stores a compact copy of the last result
privately, outside every repository."""

import json
import os
from pathlib import Path
import re
import time

from runtime import hook_delivery
from runtime import package_versions as pv
from runtime import workspace_observe as ob


CATALOG = "clients/workspace-requirements.json"
RECORD_NAME = "workspace-readiness.json"
SCHEMA_VERSION = 1
MAX_CATALOG = 65536
MAX_RECORD = 8192
BUDGET = 20
CLIENTS = tuple(ob.CLIENT_COMMANDS)
STATES = ("ready", "missing", "outdated", "needs-user", "not-supported", "unknown")
NOT_READY = ("missing", "outdated", "needs-user")
EXIT = {"ready": 0, "not-ready": 1, "undetermined": 2}
REASONS = {"cli-missing": "command not found on PATH", "timeout": "command timed out",
           "output-too-large": "command output was too large",
           "time-budget": "time budget used up before this check finished", "cli-failed": "command failed",
           "unrecognized-output": "command output not recognized"}
SIGNED_OUT = re.compile(r"not logged in", re.IGNORECASE)


def load_catalog(package):
    """The requirements the command checks; ValueError when the file is unusable."""
    catalog = pv._read_json(Path(package) / CATALOG, MAX_CATALOG)
    items = catalog.get("items")
    if (catalog.get("schema_version") != SCHEMA_VERSION or catalog.get("clients") != list(CLIENTS) or not isinstance(items, list) or
            not re.fullmatch(r"[0-9a-f]{40}", str(catalog.get("toolkit_revision")))):
        raise ValueError("The workspace requirements file is not usable")
    for item in items:
        checks = item.get("check") if isinstance(item, dict) else None
        if (not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key] for key in ("id", "name", "why", "install_ref")) or
                item.get("scope") not in ("machine", "client") or not isinstance(item.get("user_actions"), list) or
                not isinstance(checks, dict) or (item["scope"] == "client" and set(checks) != set(CLIENTS))):
            raise ValueError("The workspace requirements file is not usable")
    return catalog


def cell(state, reason=None):
    return dict(state=state, reason=reason) if reason else dict(state=state)


def _unobserved(reason):
    return cell("unknown", reason)


def check_cli(context, spec):
    """A command that must exist, optionally at a minimum version and signed in."""
    command = spec["argv"][0]
    result = context.observer.run(spec["argv"])
    if result.get("reason") == "cli-missing":
        return cell("missing", f"{command} is not on PATH")
    if "reason" in result:
        return _unobserved(result["reason"])
    match = ob.VERSION_TEXT.search(result["out"]) if result["code"] == 0 else None
    if not match:
        return _unobserved("cli-failed" if result["code"] else "unrecognized-output")
    minimum = spec.get("minimum")
    if minimum and tuple(map(int, match[0].split("."))) < tuple(map(int, minimum.split("."))):
        return cell("outdated", f"{command} is {match[0]}; {minimum} or newer is needed")
    if spec.get("sign_in_argv"):
        auth = context.observer.run(spec["sign_in_argv"])
        if "reason" in auth:
            return _unobserved(auth["reason"])
        if auth["code"] == 0:
            return cell("ready")
        return cell("needs-user", "not signed in") if SIGNED_OUT.search(auth["out"]) else _unobserved("sign-in state is not readable")
    return cell("ready")


def check_package(context, client):
    """The vibe-workflow plugin at this package's version, read through package_versions."""
    text, reason = ob.listing(context.observer, client, "plugin")
    if text is None:
        return _unobserved(reason)
    parsed = (pv.parse_codex if client == "codex" else pv.parse_claude)(text)
    if parsed["state"] == "unknown":
        return _unobserved("unrecognized-output")
    if parsed["state"] == "not-installed":
        return cell("missing", f"{pv.PLUGIN} is not installed in {client}")
    enabled = [install for install in parsed["installs"] if install["status"] == "enabled"]
    if not enabled:
        return cell("missing", f"{pv.PLUGIN} is installed but disabled")
    if context.expected is None:
        return _unobserved("the package version is not readable")
    stale = sorted({install["version"] for install in enabled if install["version"] != context.expected})
    if stale:
        return cell("outdated", f"{client} runs {', '.join(stale)}; this package is {context.expected}")
    return cell("ready")


def _snake(event):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def _hook_keys(package):
    """The trust keys Codex keeps for this package's hooks: `<event>:<group>:<handler>`."""
    groups = pv._read_json(Path(package) / "hooks" / "codex.json", MAX_CATALOG).get("hooks")
    if not isinstance(groups, dict):
        raise ValueError("Unreadable hook definitions")
    return [f"{_snake(event)}:{group}:{handler}" for event, entries in groups.items()
            for group, entry in enumerate(entries) for handler in range(len(entry["hooks"]))]


def _approvals(home):
    """The hook approvals in Codex's config; `{}` when it has none or the file is absent, ValueError when unusable."""
    try:
        hooks = ob.read_toml(home / "config.toml").get("hooks", {})
    except FileNotFoundError:
        return {}
    approvals = hooks.get("state", {}) if isinstance(hooks, dict) else None
    if not isinstance(approvals, dict):
        raise ValueError("Unrecognized hook approvals")
    return approvals


def _approved(entry, content):
    """An approval counts when it is not disabled and carries its hash (and, for a plugin's hooks, its content hash),
    as runtime/setup_observation.py requires."""
    return (isinstance(entry, dict) and entry.get("enabled") is not False and bool(entry.get("trusted_hash")) and
            (not content or bool(entry.get("trusted_content_hash"))))


def check_hooks(context, client):
    """`ready` only when the client's own startup hook left a delivery note for the current package. Codex also
    records approvals in its config, so missing ones are visible as `needs-user`; Claude Code lists nothing."""
    text, reason = ob.listing(context.observer, client, "plugin")
    if text is None:
        return _unobserved(reason)
    parsed = (pv.parse_codex if client == "codex" else pv.parse_claude)(text)
    if parsed["state"] == "unknown":
        return _unobserved("unrecognized-output")
    enabled = [install for install in parsed["installs"] if install["status"] == "enabled"]
    if not enabled:
        return cell("missing", f"{pv.PLUGIN} is not installed and enabled in {client}")
    if client == "codex":
        try:
            keys = _hook_keys(context.package)
        except (OSError, ValueError, KeyError, TypeError):
            return _unobserved("the package hook definitions are not readable")
        try:
            approvals = _approvals(ob.client_home(client, context.environ))
        except ValueError:
            return _unobserved("the client configuration is not readable")
        untrusted = 0
        for install in enabled:
            prefix = f"{pv.PLUGIN}@{install['marketplace']}:hooks/codex.json:"
            untrusted += sum(1 for key in keys if not _approved(approvals.get(prefix + key), content=True))
        if untrusted:
            return cell("needs-user", f"{untrusted} hook approvals are missing or disabled; the user must approve them in {client}")
    seen = hook_delivery.delivery(context.package, client, context.environ)
    if seen:
        return cell("ready", f"hooks ran {seen}")
    return _unobserved("start a new session")


PLUGIN_PARSERS = {"codex": ob.codex_plugins, "claude-code": ob.claude_plugins, "opencode": ob.opencode_plugins}
MCP_PARSERS = {"codex": ob.codex_mcp, "claude-code": ob.claude_mcp, "opencode": ob.opencode_mcp}


def check_plugin(context, client, spec):
    entries, reason = _plugins(context, client)
    if entries is None:
        return _unobserved(reason)
    return cell(*ob.plugin_state(entries, spec["names"]))


def _plugins(context, client):
    """`(entries, None)` from the client's plugin listing or `(None, reason)`. OpenCode adds the plugins
    named in its config, which its listing omits for local paths."""
    text, reason = ob.listing(context.observer, client, "plugin")
    if text is None:
        return None, reason
    entries = PLUGIN_PARSERS[client](text)
    if entries is None:
        return None, "unrecognized-output"
    if client == "opencode":
        try:
            entries = entries + ob.opencode_config_plugins(context.environ)
        except ValueError:
            return None, "the client configuration is not readable"
    return entries, None


def check_mcp(context, client, spec):
    """A server configured under its exact name. Codex lists its configuration; Claude Code and OpenCode are
    read from their config files, plus plugins that declare the server. Nothing is started or contacted."""
    names = spec["names"]
    plugin_names = spec.get("plugin_names")
    plugins, plugin_reason = _plugins(context, client) if client == "claude-code" or plugin_names else (None, None)
    try:
        if client == "codex":
            text, reason = ob.listing(context.observer, client, "mcp")
            entries = ob.codex_mcp(text) if text is not None else None
            if entries is None:
                return _unobserved(reason or "unrecognized-output")
        elif client == "claude-code":
            entries = ob.claude_mcp(context.environ, plugins)
        else:
            entries = ob.opencode_mcp(context.environ)
    except ValueError:
        return _unobserved("the client configuration is not readable")
    state, detail = ob.mcp_state(entries, names)
    if state != "missing" or detail != "not configured in this client":
        return cell(state, detail)
    if plugin_names:
        if plugins is None:
            return _unobserved(plugin_reason)
        state, detail = ob.plugin_state(plugins, plugin_names)
        if state == "ready" or detail == "installed but disabled":
            return cell(state, detail)
    elif client == "claude-code" and plugins is None:
        return _unobserved(plugin_reason)
    return cell("missing", "not configured in this client")


def _rtk_codex(home):
    """RTK's Codex setup is files: RTK.md, a reference to it in AGENTS.md and a hook in hooks.json, and the
    hook must be approved. Returns `(absent, unapproved)`."""
    absent = []
    try:
        ob.read_text(home / "RTK.md")
    except FileNotFoundError:
        absent.append("RTK.md")
    try:
        if "RTK.md" not in ob.read_text(home / "AGENTS.md"):
            absent.append("the AGENTS.md reference")
    except FileNotFoundError:
        absent.append("the AGENTS.md reference")
    positions = []
    try:
        groups = ob.read_json(home / "hooks.json").get("hooks", {})
        positions = [f"{_snake(event)}:{group}:{handler}" for event, entries in groups.items()
                     for group, entry in enumerate(entries) for handler, hook in enumerate(entry["hooks"])
                     if "rtk hook" in hook.get("command", "")]
    except FileNotFoundError:
        pass
    except (KeyError, TypeError, AttributeError):
        raise ValueError("Unrecognized hooks file") from None
    if not positions:
        absent.append("the hook")
        return absent, False
    approvals = _approvals(home)
    paths = {str(home / "hooks.json"), str((home / "hooks.json").resolve())}
    return absent, not all(any(_approved(approvals.get(f"{path}:{position}"), content=False) for path in paths) for position in positions)


def check_rtk(context, client):
    version = context.observer.run(("rtk", "--version"))
    if version.get("reason") == "cli-missing":
        return cell("missing", "rtk is not on PATH")
    if "reason" in version or version["code"] != 0:
        return _unobserved(version.get("reason", "cli-failed"))
    if client == "codex":
        try:
            absent, unapproved = _rtk_codex(ob.client_home(client, context.environ))
        except (ValueError, OSError):
            return _unobserved("the client configuration is not readable")
        if absent:
            return cell("missing", f"RTK is missing {', '.join(absent)} in codex")
        return cell("needs-user", "RTK's hook is not approved in codex; the user must approve it") if unapproved else cell("ready")
    shown = context.observer.run(("rtk", "init", "--show"))
    if "reason" in shown or shown["code"] != 0:
        return _unobserved(shown.get("reason", "cli-failed"))
    marks = ob.rtk_marks(shown["stdout"])
    labels = ("Hook", "settings.json") if client == "claude-code" else ("OpenCode",)
    if marks is None or any(label not in marks for label in labels):
        return _unobserved("unrecognized-output")
    off = [label for label in labels if not marks[label]]
    return cell("missing", f"RTK setup for {client} is incomplete ({', '.join(off)})") if off else cell("ready")


def check_skill(context, client, spec):
    """The skill file in the client's config directory or in a shared home location the client also reads."""
    home, shared = ob.client_home(client, context.environ), ob.home_dir(context.environ)
    paths = [home / path for path in spec["paths"]] + [shared / path for path in spec.get("home_paths", [])]
    return cell("ready") if any(path.is_file() for path in paths) else cell("missing", f"skill not installed in {client}")


def run_check(context, spec, client):
    kind = spec["kind"]
    if kind == "not-supported":
        return cell("not-supported", spec["reason"])
    if kind == "unobservable":
        return _unobserved(spec["reason"])
    if kind == "cli":
        return check_cli(context, spec)
    if client not in context.installed:
        return _unobserved("cli-missing")
    simple = {"package": check_package, "hooks-trust": check_hooks, "rtk": check_rtk}
    if kind in simple:
        return simple[kind](context, client)
    return {"plugin": check_plugin, "mcp": check_mcp, "skill": check_skill}[kind](context, client, spec)


class Context:
    def __init__(self, package, environ, observer, installed):
        self.package, self.environ, self.observer, self.installed = package, environ, observer, installed
        self.expected = pv.package_version(package)


def discover(observer, session, environ):
    """Each supported client: command on PATH, version, whether its config directory exists."""
    found = []
    for client, command in ob.CLIENT_COMMANDS.items():
        installed = observer.which(command) is not None
        version, reason = ob.cli_version(observer, command) if installed else (None, "cli-missing")
        entry = dict(client=client, installed=installed, cli_version=version, config_dir=ob.client_home(client, environ).is_dir(),
                     session=client == session)
        if installed and version is None:
            entry["reason"] = reason
        found.append(entry)
    return found


def overall(items):
    states = {value["state"] for item in items for value in item["cells"].values()}
    if states & set(NOT_READY):
        return "not-ready"
    return "undetermined" if "unknown" in states else "ready"


def status(package, environ=None, client=None, budget=BUDGET):
    """Every requirement for every installed client and the session's client; writes nothing."""
    environ = dict(os.environ if environ is None else environ)
    observer = ob.Observer(environ, budget)
    catalog = load_catalog(package)
    session = ob.session_client(environ, client)
    clients = discover(observer, session, environ)
    present = [entry["client"] for entry in clients if entry["installed"] or entry["session"]]
    context = Context(package, environ, observer, {entry["client"] for entry in clients if entry["installed"]})
    items, notes = [], [] if present else ["No supported client was found on PATH and none is the session's client."]
    for item in catalog["items"]:
        if item["scope"] == "machine":
            cells = dict(machine=run_check(context, item["check"], None))
        else:
            cells = {name: run_check(context, item["check"][name], name) for name in present}
        items.append(dict(id=item["id"], name=item["name"], why=item["why"], scope=item["scope"], cells=cells))
    result = "undetermined" if notes else overall(items)
    return dict(ok=True, result=result, exit_code=EXIT[result], observed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                package_version=context.expected, session_client=session, budget_seconds=budget, clients=clients,
                items=items, notes=notes)


def record_path(environ=None):
    return pv.record_path(environ).with_name(RECORD_NAME)


def summarize(report):
    """The small record startup reads: result, time and one state per cell."""
    cells = {item["id"]: {key: value["state"] for key, value in item["cells"].items()} for item in report["items"]}
    return dict(schema_version=SCHEMA_VERSION, package=pv.PLUGIN, package_version=report["package_version"],
                observed_at=report["observed_at"], result=report["result"], session_client=report["session_client"],
                clients=[entry["client"] for entry in report["clients"] if entry["installed"] or entry["session"]], cells=cells,
                not_ready=[f"{item}:{key}:{state}" for item, row in cells.items() for key, state in row.items() if state in NOT_READY])


def read_record(path):
    """The record, or None when absent, oversized, malformed or of another schema."""
    try:
        record = pv._read_json(path, MAX_RECORD)
    except (OSError, ValueError):
        return None
    if (record.get("schema_version") != SCHEMA_VERSION or record.get("package") != pv.PLUGIN or
            record.get("result") not in EXIT or not isinstance(record.get("observed_at"), str) or
            not isinstance(record.get("cells"), dict) or not isinstance(record.get("not_ready"), list)):
        return None
    return record


READINESS_ACTION = "run prepare-my-workspace"
READINESS_LISTED = 3
CHECKED_AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def readiness_warning(package, client, environ=None):
    """One short line for startup when the last record lists required items missing for this client or the machine.

    Reads one small private file and the requirements catalog; only item IDs the catalog lists are named, so a
    record cannot put its own text into the startup message. Absent, corrupt or ready records give None."""
    record = read_record(record_path(environ))
    if record is None or not CHECKED_AT.fullmatch(record["observed_at"]):
        return None
    known = {item["id"] for item in load_catalog(package)["items"]}
    found = []
    for entry in record["not_ready"]:
        parts = entry.split(":") if isinstance(entry, str) else []
        if len(parts) == 3 and parts[0] in known and parts[1] in ("machine", client) and parts[2] in NOT_READY:
            found.append(f"{parts[0]} {parts[2]}")
    if not found:
        return None
    more = f" and {len(found) - READINESS_LISTED} more" if len(found) > READINESS_LISTED else ""
    return (f"Workspace not ready for {client} (checked {record['observed_at']}): "
            f"{', '.join(found[:READINESS_LISTED])}{more}. Next action: {READINESS_ACTION}.")


def write_record(path, record):
    data = (json.dumps(record, indent=1) + "\n").encode()
    if len(data) > MAX_RECORD:
        raise ValueError("Record is too large")
    pv.write_private(path, data, ".workspace-readiness-")


def record(package, environ=None, client=None, budget=BUDGET):
    """Run `status` and store its compact result; the command's own result is not a failure to record."""
    report = status(package, environ, client, budget)
    try:
        write_record(record_path(environ), summarize(report))
    except (OSError, ValueError):
        return report | dict(ok=False, recorded=False, error="The record could not be written")
    return report | dict(recorded=True)


def render(report):
    """A plain table of states, then the reason for every state that is not ready."""
    keys = ["machine", *[entry["client"] for entry in report["clients"] if entry["installed"] or entry["session"]]]
    width = max(len(item["name"]) for item in report["items"])
    lines = [f"Workspace readiness: {report['result']} (observed {report['observed_at']})"]
    lines.append("Clients: " + (", ".join(
        f"{entry['client']} {entry['cli_version'] or 'version unknown'}" + (" (session)" if entry["session"] else "") +
        ("" if entry["installed"] else " (command not found)") for entry in report["clients"]) or "none"))
    lines += ["", f"{'Item'.ljust(width)}  " + "  ".join(key.ljust(13) for key in keys)]
    for item in report["items"]:
        lines.append(f"{item['name'].ljust(width)}  " + "  ".join(
            item["cells"].get(key, {}).get("state", "").ljust(13) for key in keys))
    details = [f"- {item['name']} ({key}): {value['state']}, {REASONS.get(value['reason'], value['reason'])}"
               for item in report["items"] for key, value in item["cells"].items() if value.get("reason")]
    if details:
        lines += ["", "Details:", *details]
    lines += [f"Note: {note}" for note in report["notes"]]
    return "\n".join(lines)
