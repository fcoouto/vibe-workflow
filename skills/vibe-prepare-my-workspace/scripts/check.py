#!/usr/bin/env python3
"""Read-only check of the partner tools OpenCode relies on. Never installs or writes."""
import argparse
from dataclasses import dataclass
import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

STATES = ("ready", "missing", "outdated", "unknown", "not-supported")
PRECEDENCE = ("missing", "outdated", "unknown", "not-supported", "ready")


def parse_version(text):
    match = re.search(r"\d+(?:\.\d+)*", text)
    if not match:
        raise ValueError("no_version")
    return tuple(int(part) for part in match.group().split("."))


def _is_prerelease(text):
    match = re.search(r"\d+(?:\.\d+)*(-[0-9A-Za-z])?", text)
    return bool(match and match.group(1))


def compare(installed, latest):
    a, b = parse_version(installed), parse_version(latest)
    size = max(len(a), len(b))
    a, b = a + (0,) * (size - len(a)), b + (0,) * (size - len(b))
    return (a > b) - (a < b) or _is_prerelease(latest) - _is_prerelease(installed)


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


def _frontmatter_version(path):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines[1:] if lines and lines[0] == "---" else ():
        if line == "---":
            break
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


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
    if not found or not pattern:
        return found, None
    if pattern == "frontmatter" and kind == "skill":
        return found, _frontmatter_version(base / "skills" / spec["name"] / "SKILL.md")
    return found, _cached_version(pattern, home)


ALLOWED_HOSTS = ("registry.npmjs.org", "api.github.com", "raw.githubusercontent.com")
VERSION_KEY = re.compile(r'"version"\s*:\s*"([^"]+)"')


def _allowed(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS


class AllowedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect_not_allowed", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "vibe-workflow-workspace-check"})
    try:
        with urllib.request.build_opener(AllowedRedirects).open(request, timeout=5) as response:
            return response.read(1 << 20).decode("utf-8")
    except UnicodeDecodeError as error:
        raise OSError("bad_encoding") from error


def _json_field(body, field):
    try:
        value = json.loads(body)[field]
        parse_version(value)
    except (ValueError, KeyError, TypeError) as error:
        raise LookupError("bad_response") from error
    return value


def latest_version(spec, fetch):
    kind = spec.get("type")
    if kind == "npm":
        url, field = f"https://registry.npmjs.org/{spec['package'].replace('/', '%2F')}/latest", "version"
    elif kind == "github":
        url, field = f"https://api.github.com/repos/{spec['repo']}/releases/latest", "tag_name"
    elif kind == "raw":
        url, field = spec["url"], None
    else:
        raise LookupError("unsupported_source")
    if not _allowed(url):
        raise LookupError("host_not_allowed")
    try:
        body = fetch(url)
    except (OSError, http.client.HTTPException) as error:
        raise LookupError("lookup_failed") from error
    if field:
        return _json_field(body, field)
    found = VERSION_KEY.search(body)
    text = found.group(1) if found else body.strip()
    if not found and not re.fullmatch(r"v?\d+(\.\d+)*(-[0-9A-Za-z.]+)?", text):
        raise LookupError("bad_response")
    return text


@dataclass
class Env:
    home: Path
    run: Callable[[list], str]
    fetch: Callable[[str], str]


def real_env():
    def run(argv):
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("command_timeout") from error
        if done.returncode:
            raise OSError("nonzero_exit")
        return done.stdout + done.stderr
    return Env(home=Path.home(), run=run, fetch=http_fetch)


def _dotted(text):
    return ".".join(map(str, parse_version(text)))


def _shown(raw):
    try:
        return raw and _dotted(raw)
    except ValueError:
        return None


def judge(partner, config, env):
    def row(state, reason=None, installed=None, upstream=None):
        return {"id": partner["id"], "state": state, "installed": installed, "upstream": upstream,
                "why": partner["why"], "reason": reason}

    raw, machine = None, partner.get("machine")
    if machine:
        try:
            output = env.run([machine["command"], *machine["version_args"]])
        except FileNotFoundError:
            return row("missing", "command_not_found")
        except OSError:
            return row("unknown", "command_failed")
        try:
            parse_version(output)
        except ValueError:
            return row("unknown", "unreadable_version")
        raw = output
    spec = partner.get("clients", {}).get("opencode")
    if spec:
        if config is None:
            return row("unknown", "config_unreadable", _shown(raw))
        found, local = integration_present(spec, config, env.home)
        if not found:
            return row("missing", "integration_missing", _shown(raw))
        raw = raw or local
    upstream = partner["upstream"]
    kind = upstream["type"]
    if kind in ("hosted", "presence"):
        return row("ready", installed=_shown(raw))
    if kind == "none":
        return row("unknown", "no_upstream_source", _shown(raw))
    installed = _shown(raw)
    if not installed:
        return row("unknown", "no_comparable_version")
    if kind == "minimum":
        latest, behind = upstream["version"], "below_minimum"
    else:
        try:
            latest, behind = latest_version(upstream, env.fetch), "behind_upstream"
        except LookupError as error:
            return row("unknown", str(error), installed)
    if compare(raw, latest) < 0:
        return row("outdated", behind, installed, _dotted(latest))
    return row("ready", installed=installed, upstream=_dotted(latest))


def _load_config(home):
    merged = {"plugins": [], "plugin": [], "mcp": {"servers": {}}}
    for name in ("opencode.json", "opencode.jsonc"):
        path = home / ".config/opencode" / name
        if not path.exists():
            continue
        try:
            config = read_config(path)
        except ValueError:
            return None
        for key in ("plugins", "plugin"):
            if isinstance(config.get(key), list):
                merged[key] += config[key]
        mcp = config.get("mcp")
        if isinstance(mcp, dict):
            servers = mcp.get("servers")
            merged["mcp"]["servers"].update(servers if isinstance(servers, dict) else {})
            merged["mcp"].update({key: value for key, value in mcp.items() if key != "servers"})
    return merged


def _table(rows):
    columns = ("id", "state", "installed", "upstream", "reason")
    cells = [columns] + [tuple(str(row[c] or "-") for c in columns) for row in rows]
    widths = [max(len(line[i]) for line in cells) for i in range(len(columns))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip() for line in cells)


def main(argv=None, env=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partners", default=str(Path(__file__).resolve().parent.parent / "partners.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    env = env or real_env()
    partners = json.loads(Path(args.partners).read_text())["partners"]
    config = _load_config(env.home)
    rows = [judge(partner, config, env) for partner in partners]
    print(json.dumps({"client": "opencode", "rows": rows}, indent=2) if args.json else _table(rows))
    return exit_code([row["state"] for row in rows])


if __name__ == "__main__":
    sys.exit(main())
