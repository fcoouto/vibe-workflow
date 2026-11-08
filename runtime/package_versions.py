"""Package version, observed client installs and the expected-version record.

Installs are observed only through each client's own CLI. Startup uses `drift_warning`, which reads
two small files and never writes, spawns a process or uses the network."""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time


PLUGIN = "vibe-workflow"
CLIENTS = {"codex": ("codex", "plugin", "list", "--json"), "claude-code": ("claude", "plugin", "list", "--json")}
MANIFESTS = {"codex": ".codex-plugin/plugin.json", "claude-code": ".claude-plugin/plugin.json"}
RECORD_NAME = "package-release.json"
SCHEMA_VERSION = 1
MAX_RECORD = 4096
MAX_MANIFEST = 65536
MAX_OUTPUT = 8 * 1024 * 1024
CLI_TIMEOUT = 10
VERSION = re.compile(r"\d+\.\d+\.\d+")
CLI_VERSION = re.compile(r"[0-9][0-9A-Za-z.+_-]*")
NEXT_ACTION = "run the prepare-my-workspace upgrade"


def _read_json(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not os.fstat(descriptor).st_mode & 0o170000 == 0o100000:
            raise ValueError("Expected a regular file")
        data = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise ValueError("File is too large")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def manifest_version(package, client):
    """The version a client's own manifest declares, or None when unreadable."""
    if client not in MANIFESTS:
        return None
    try:
        version = _read_json(Path(package) / MANIFESTS[client], MAX_MANIFEST).get("version")
    except (OSError, ValueError):
        return None
    return version if isinstance(version, str) and VERSION.fullmatch(version) else None


def package_version(package):
    versions = {manifest_version(package, client) for client in MANIFESTS}
    return versions.pop() if len(versions) == 1 else None


def record_path(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_STATE_HOME")
    if not base or not os.path.isabs(base):
        base = os.path.join(environ.get("HOME") or str(Path.home()), ".local", "state")
    return Path(base) / PLUGIN / RECORD_NAME


def read_record(path):
    """The record, or None when absent, oversized, malformed or of another schema."""
    try:
        record = _read_json(path, MAX_RECORD)
    except (OSError, ValueError):
        return None
    if (record.get("schema_version") != SCHEMA_VERSION or record.get("package") != PLUGIN or
            not isinstance(record.get("version"), str) or not VERSION.fullmatch(record["version"]) or
            not isinstance(record.get("clients"), dict)):
        return None
    return record


def _private_directory(directory):
    """Create the state directory as 0700, or accept an existing one of ours without changing its mode."""
    created = False
    try:
        directory.mkdir(mode=0o700, parents=True)
        created = True
    except FileExistsError:
        pass
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("The state directory must be a real directory owned by the current user")
    if created:
        os.chmod(directory, 0o700)


def write_record(path, version, clients):
    path = Path(path)
    record = dict(schema_version=SCHEMA_VERSION, package=PLUGIN, version=version,
                  recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), clients=clients)
    data = (json.dumps(record, indent=2) + "\n").encode()
    if len(data) > MAX_RECORD:
        raise ValueError("Record is too large")
    _private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".package-release-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _unknown():
    return dict(state="unknown", installs=[])


def _installs(entries, key, describe):
    """Installs of this plugin among the entries. `describe` returns an install, {} to skip an entry
    that is not installed, or None for an unexpected shape, which makes the whole listing unreadable."""
    installs = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get(key), str):
            return None
        name, separator, marketplace = entry[key].partition("@")
        if name != PLUGIN or not separator or not marketplace:
            continue
        install = describe(entry, marketplace)
        if install is None:
            return None
        if install:
            installs.append(install)
    return installs


def _load(text):
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _optional_text(value):
    return value if value is None or isinstance(value, str) else False


def parse_codex(text):
    """Entries for `vibe-workflow@<marketplace>` from `codex plugin list --json`; unknown when unreadable."""
    data = _load(text)
    if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
        return _unknown()

    def describe(entry, marketplace):
        source = entry.get("source")
        if isinstance(source, dict):
            source = next((source[key] for key in ("path", "url", "source") if isinstance(source.get(key), str)), None)
        version = entry.get("version")
        if (not isinstance(entry.get("installed"), bool) or not isinstance(entry.get("enabled"), bool) or
                not isinstance(version, str) or not CLI_VERSION.fullmatch(version) or _optional_text(source) is False):
            return None
        return dict(marketplace=marketplace, version=version, source=source,
                    status="enabled" if entry["enabled"] else "disabled") if entry["installed"] else {}

    installs = _installs(data["installed"], "pluginId", describe)
    if installs is None:
        return _unknown()
    return dict(state="installed" if installs else "not-installed", installs=installs)


def parse_claude(text):
    """Entries for `vibe-workflow@<marketplace>` from `claude plugin list --json`; unknown when unreadable."""
    data = _load(text)
    if not isinstance(data, list):
        return _unknown()

    def describe(entry, marketplace):
        version, source = entry.get("version"), entry.get("readFromFolder")
        if (not isinstance(entry.get("enabled"), bool) or not isinstance(version, str) or
                not CLI_VERSION.fullmatch(version) or _optional_text(source) is False):
            return None
        return dict(marketplace=marketplace, version=version, source=source,
                    status="enabled" if entry["enabled"] else "disabled")

    installs = _installs(data, "id", describe)
    if installs is None:
        return _unknown()
    return dict(state="installed" if installs else "not-installed", installs=installs)


def _run(argv, **options):
    return subprocess.run(argv, **options)


def observe(client, run=None):
    """One client's installs, read through its official CLI; unknown carries the reason."""
    run = run or _run
    argv = list(CLIENTS[client])
    parse = parse_codex if client == "codex" else parse_claude
    try:
        result = run(argv, capture_output=True, timeout=CLI_TIMEOUT, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return dict(client=client, state="unknown", reason="cli-missing", installs=[])
    except subprocess.TimeoutExpired:
        return dict(client=client, state="unknown", reason="timeout", installs=[])
    except (OSError, ValueError):
        return dict(client=client, state="unknown", reason="cli-failed", installs=[])
    if result.returncode != 0:
        return dict(client=client, state="unknown", reason="cli-failed", installs=[])
    try:
        if len(result.stdout) > MAX_OUTPUT:
            raise ValueError("Output is too large")
        parsed = parse(result.stdout.decode("utf-8"))
    except ValueError:
        parsed = _unknown()
    if parsed["state"] == "unknown":
        return dict(client=client, state="unknown", reason="unrecognized-output", installs=[])
    return dict(client=client, **parsed)


def _compare(observation, expected):
    installs = observation["installs"]
    observation["matches"] = (all(install["version"] == expected for install in installs)
                              if observation["state"] == "installed" else None)
    return observation


def status(package, environ=None, run=None):
    """The package version, the record and each client's observed install; writes nothing."""
    expected = package_version(package)
    path = record_path(environ)
    current = read_record(path)
    if current is not None:
        summary = dict(state="valid", version=current["version"], recorded_at=current.get("recorded_at"))
    else:
        summary = dict(state="invalid" if os.path.lexists(path) else "absent")
    return dict(ok=True, package_version=expected, record=summary,
                clients=[_compare(observe(client, run), expected) for client in CLIENTS])


def record(package, environ=None, run=None):
    """Write the expected-version record only when every installed client reports the package version."""
    report = status(package, environ, run)
    expected = report["package_version"]
    clients = report["clients"]
    unreadable = [c for c in clients if c["state"] == "unknown"]
    differing = [c["client"] for c in clients if c["matches"] is False]
    confirmed = [c for c in clients if c["matches"]]
    absent = [c["client"] for c in clients if c["state"] == "not-installed"]
    result = dict(ok=False, recorded=False, version=expected, clients=clients, not_installed=absent)
    if expected is None:
        return result | dict(error="The client manifests do not declare one package version")
    if differing or unreadable:
        parts = ([f"{', '.join(differing)} report another version than {expected}"] if differing else []) + \
                [f"{c['client']} could not be read ({c['reason']}) and is not upgraded" for c in unreadable]
        return result | dict(error="; ".join(parts) + "; fix that client or retry, then record again")
    if not confirmed:
        return result | dict(error=f"No installed client reports {expected}; nothing to record")
    try:
        write_record(record_path(environ), expected,
                     {c["client"]: dict(version=expected, marketplaces=[i["marketplace"] for i in c["installs"]])
                      for c in confirmed})
    except (OSError, ValueError):
        return result | dict(error="The record could not be written")
    return result | dict(ok=True, recorded=True)


def drift_warning(package, client, environ=None):
    """One plain warning when this client's manifest differs from the record; otherwise None."""
    expected = read_record(record_path(environ))
    own = manifest_version(package, client)
    if expected is None or own is None or own == expected["version"]:
        return None
    return (f"Vibe Coding Workflow package drift: {client} runs version {own} but this machine expects "
            f"{expected['version']}. Next action: {NEXT_ACTION}.")


def append_warning(output, warning):
    """A copy of a SessionStart output with the warning added to its message and context."""
    specific = output.get("hookSpecificOutput") if isinstance(output, dict) else None
    if not isinstance(specific, dict) or not isinstance(specific.get("additionalContext"), str):
        return output
    merged = dict(output, hookSpecificOutput=dict(specific, additionalContext=(specific["additionalContext"] + " " + warning).strip()))
    merged["systemMessage"] = (str(output.get("systemMessage", "")) + " " + warning).strip()
    return merged
