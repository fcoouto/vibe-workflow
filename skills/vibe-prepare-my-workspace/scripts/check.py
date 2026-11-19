#!/usr/bin/env python3
"""Read-only check of the partner tools OpenCode relies on. Never installs or writes."""
import json
from pathlib import Path
import re

STATES = ("ready", "missing", "outdated", "unknown", "not-supported")
PRECEDENCE = ("missing", "outdated", "unknown", "not-supported", "ready")


def parse_version(text):
    match = re.search(r"\d+(?:\.\d+)*", text)
    if not match:
        raise ValueError("no_version")
    return tuple(int(part) for part in match.group().split("."))


def compare(installed, latest):
    a, b = parse_version(installed), parse_version(latest)
    size = max(len(a), len(b))
    a, b = a + (0,) * (size - len(a)), b + (0,) * (size - len(b))
    return (a > b) - (a < b)


def worst(states):
    return min(states, key=PRECEDENCE.index)


def exit_code(states):
    if any(state in ("missing", "outdated") for state in states):
        return 1
    return 2 if "unknown" in states else 0


def _string_end(text, start):
    end = start + 1
    while end < len(text) and text[end] != '"':
        end += 2 if text[end] == "\\" else 1
    return end + 1


def _strip_comments(text):
    out, i = [], 0
    while i < len(text):
        if text[i] == '"':
            end = _string_end(text, i)
            out.append(text[i:end])
            i = end
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("config_unreadable")
            i = end + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _strip_trailing_commas(text):
    out, i = [], 0
    while i < len(text):
        if text[i] == '"':
            end = _string_end(text, i)
            out.append(text[i:end])
            i = end
            continue
        if text[i] == ",":
            ahead = i + 1
            while ahead < len(text) and text[ahead].isspace():
                ahead += 1
            if ahead < len(text) and text[ahead] in "}]":
                i += 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def read_config(path):
    try:
        value = json.loads(_strip_trailing_commas(_strip_comments(Path(path).read_text())))
    except (OSError, ValueError) as error:
        raise ValueError("config_unreadable") from error
    if not isinstance(value, dict):
        raise ValueError("config_unreadable")
    return value


def _plugin_names(config):
    for key in ("plugins", "plugin"):
        entries = config.get(key)
        for entry in entries if isinstance(entries, list) else ():
            name = entry[0] if isinstance(entry, list) and entry else entry
            if isinstance(name, str):
                yield name


def _cached_version(pattern, home):
    versions = []
    for path in (home / ".cache/opencode/npm").glob(pattern):
        try:
            versions.append((path.stat().st_mtime, json.loads(path.read_text())["version"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(versions)[1] if versions else None


def integration_present(spec, config, home):
    kind, base = spec["kind"], home / ".config/opencode"
    if kind == "plugin":
        found = any(spec["match"] in name for name in _plugin_names(config))
    elif kind == "mcp":
        mcp = config.get("mcp")
        servers = mcp.get("servers") if isinstance(mcp, dict) else None
        found = isinstance(mcp, dict) and (spec["name"] in mcp or isinstance(servers, dict) and spec["name"] in servers)
    elif kind == "skill":
        found = (base / "skills" / spec["name"]).exists()
    elif kind == "file":
        found = (base / spec["path"]).is_file()
    else:
        raise ValueError("unsupported_integration")
    pattern = spec.get("version_from")
    return found, _cached_version(pattern, home) if found and pattern else None
