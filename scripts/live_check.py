#!/usr/bin/env python3
"""Dev-only live check: load this checkout's package into a throwaway clone and run one OpenCode session.

Never exported. Creates a temporary clone with a project-local plugin copy, sends each prompt to one
API-created root session, prints a JSON summary, and always removes the session and the clone.
"""
import os
import sys

# Run as a program, Python puts scripts/ first on the path, where scripts/select.py shadows the standard
# `select` module that subprocess needs. Drop exactly that entry before importing anything else.
if sys.path and sys.path[0] and os.path.realpath(sys.path[0]) == os.path.dirname(os.path.realpath(__file__)):
    del sys.path[0]

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = 'id: "vibe-workflow",'
LIVE_ID = 'id: "vibe-workflow-live",'
RECORD = ROOT / "tests" / "opencode-verified.json"
INJECTION_COMMAND = "python3 -B scripts/live_check.py --injection"
GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=live-check",
       "-c", "user.email=live-check@example.invalid", "-c", "commit.gpgsign=false"]


def rename_plugin_id(text):
    if text.count(PLUGIN_ID) != 1:
        raise ValueError("expected exactly one plugin id")
    return text.replace(PLUGIN_ID, LIVE_ID)


def parse_version(text):
    match = re.fullmatch(r"opencode v(\d+\.\d+\.\d+)\s*", text or "")
    return match.group(1) if match else None


def read_record(path):
    """The live-verified record: `models` always, `version` and `verified` once a live check passed."""
    try:
        record = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise ValueError(f"no live-verified record at {path}") from None
    except (OSError, ValueError) as error:
        raise ValueError(f"unreadable live-verified record {path}: {error}") from None
    models = record.get("models") if isinstance(record, dict) else None
    if not isinstance(models, list) or not models or not all(isinstance(model, str) and model for model in models):
        raise ValueError(f"live-verified record {path} needs a non-empty list of model ids under 'models'")
    return record


def version_problem(installed_text, record):
    """None when the installed OpenCode is the live-verified one; otherwise what to tell the operator."""
    installed, verified = parse_version(installed_text), record.get("version")
    if installed is None:
        return f"could not read the OpenCode version from {installed_text!r}; run `{INJECTION_COMMAND}`"
    if not verified:
        return f"OpenCode {installed} has no live-verified injection yet; run `{INJECTION_COMMAND}`"
    if installed != verified:
        return (f"OpenCode {installed} differs from the live-verified {verified}; run `{INJECTION_COMMAND}` "
                "to check that agents still receive the injected notes")
    return None


def _note(text):
    start = text.find("vibe-workflow:")
    return None if start < 0 else text[start:].split("\n", 1)[0]


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part.get("text", "") for part in value if isinstance(part, dict))
    return ""


def summarize(messages):
    events = []
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "assistant":
            continue
        for part in message.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                result = state.get("content") or state.get("output") or ""
                events.append({"kind": "tool", "name": part.get("name"), "input": state.get("input") or {},
                               "note": _note(_text(result))})
            elif part.get("type") == "text":
                events.append({"kind": "reply", "text": part.get("text", "")})
    return events


def _run(argv, cwd=None, timeout=120, data=None):
    return subprocess.run(argv, cwd=cwd, input=data, capture_output=True, text=True, timeout=timeout,
                          check=True).stdout


def _api(*args, data=None, timeout=60):
    argv = ["opencode", "api", *args] + (["-d", json.dumps(data)] if data is not None else [])
    out = _run(argv, timeout=timeout)
    if not out.strip():
        return None
    value = json.loads(out)
    return value.get("data", value) if isinstance(value, dict) else value


def prepare(work, settings=None, origin=None):
    package, project = work / "package", work / "project"
    _run([sys.executable, "-B", str(ROOT / "scripts" / "package.py"), "--output", str(package)], cwd=ROOT)
    _run(["git", "clone", "-q", str(ROOT), str(project)])
    with open(project / ".git" / "info" / "exclude", "a") as exclude:
        exclude.write("\n.opencode/\n")
    plugin = project / ".opencode" / "plugins" / "live-check"
    shutil.copytree(package, plugin)
    index = plugin / "index.js"
    index.write_text(rename_plugin_id(index.read_text()))
    if settings is not None:
        (project / ".vibe-workflow").mkdir(exist_ok=True)
        (project / ".vibe-workflow" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
        _run([*GIT, "add", ".vibe-workflow/settings.json"], cwd=project)
        _run([*GIT, "commit", "-q", "--allow-empty", "-m", "live-check settings"], cwd=project)
        _run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=project)
    if origin:
        _run(["git", "remote", "set-url", "origin", origin], cwd=project)
    return project


def check(prompts, settings=None, origin=None, keep=False, wait=900):
    work = Path(tempfile.mkdtemp(prefix="vibe-live-check-")).resolve()
    session = None
    try:
        project = prepare(work, settings, origin)
        created = _api("post", "/api/session", data={"location": {"directory": str(project)}})
        session = created.get("id") if isinstance(created, dict) else None
        if not session:
            raise ValueError(f"no session id in the create response: {created!r}")
        for prompt in prompts:
            _api("post", f"/api/session/{session}/prompt", data={"text": prompt})
            _api("post", f"/api/experimental/session/{session}/wait", timeout=wait)
        messages = _api("get", f"/api/session/{session}/message?limit=200&order=asc") or []
        title = (_api("get", f"/api/session/{session}") or {}).get("title")
        return {"session": session, "project": str(project) if keep else None, "title": title,
                "events": summarize(messages)}
    finally:
        if session and not keep:
            try:
                _api("delete", f"/api/session/{session}")
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                print(f"live-check: could not delete session {session}: {error}", file=sys.stderr)
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", action="append", required=True)
    parser.add_argument("--settings", type=json.loads)
    parser.add_argument("--origin", help="origin URL for gh lookups; use a throwaway or local URL, since the "
                        "session's agent could fetch from or push to it")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check(args.prompt, args.settings, args.origin, args.keep), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
