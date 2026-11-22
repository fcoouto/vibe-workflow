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
import datetime  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import os  # noqa: E402

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


INJECTION_PROMPT = ("Do not call any tools. Quote verbatim the description of the model parameter in your "
                    "subagent tool's input schema.")
# The user pre-authorized the injection check for exactly these models; a different list needs their approval.
APPROVED_MODELS = ("claude-agent/haiku", "openai/gpt-6.1-terra", "opencode-go/claude-haiku-5-5")
INJECTION_SETTINGS = {"version": 1, "features": [{"smart_router": {"enabled": True}},
                                                {"git_workflow": {"enabled": True}}]}
# One shell-note probe proves the execute.after hook still appends its notes to tool results: a branch switch in
# the clone (a primary checkout) must come back with the primary-checkout note.
SHELL_PROBE_MODEL = "claude-agent/haiku"
SHELL_PROMPT = ("Run exactly one shell command and nothing else: git switch -c vibe-injection-probe . "
                "Then quote verbatim every line of that command's result that starts with vibe-workflow:")
SHELL_NOTE = re.compile(r"vibe-workflow: the primary checkout \(.+?\) is now on vibe-injection-probe\. "
                        r"Never touch the primary checkout")


def note_texts(index_source):
    """MODEL_NOTE and ROUTER_NOTE as index.js defines them: `const NAME =` then "+"-joined double-quoted literals."""
    def one(name):
        match = re.search(r"const " + name + r' =\s*((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+)', index_source)
        if not match:
            raise ValueError(f"index.js defines no {name}")
        return "".join(json.loads(literal) for literal in re.findall(r'"(?:[^"\\]|\\.)*"', match.group(1)))
    return one("MODEL_NOTE"), one("ROUTER_NOTE")


def normalize(text):
    return re.sub(r"\s+", " ", (text or "").replace('\\"', '"')).strip()


def injection_passes(reply, tool_calls, notes):
    quoted = normalize(reply)
    return tool_calls == 0 and all(normalize(note) in quoted for note in notes)


def shell_probe_passes(reply, tool_calls):
    return tool_calls == 1 and SHELL_NOTE.search(normalize(reply)) is not None


def isolated_env(config_home):
    """The user's logins stay (HOME, data dirs); their OpenCode config, and so their installed plugins, do not.

    OPENCODE_* variables can point at another config (OPENCODE_CONFIG, _DIR, _CONTENT) or the caller's session.
    """
    env = {name: value for name, value in os.environ.items() if not name.startswith("OPENCODE")}
    return {**env, "XDG_CONFIG_HOME": str(config_home)}


def _events(output):
    for line in output.splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def run_model(project, model, env, prompt=INJECTION_PROMPT):
    """One first-call prompt on one model in an isolated private server; text parts form the reply."""
    # OpenCode takes its directory from PWD, which cwd alone leaves pointing at the caller; empty stdin keeps the
    # caller's input out of the prompt.
    out = _run(["opencode", "run", "--standalone", "--archived", "-m", model, "--format", "json", prompt],
               cwd=project, timeout=300, data="", env={**env, "PWD": str(project)})
    # One event per line. Only top-level `text` events are the model's reply; a `tool_use` event is one tool call,
    # and the text nested in its result is the tool's output, not the reply.
    texts, tools, sessions = {}, set(), []
    for event in _events(out):
        if not isinstance(event, dict):
            continue
        session, part = event.get("sessionID"), event.get("part")
        if isinstance(session, str) and session.startswith("ses") and session not in sessions:
            sessions.append(session)
        if not isinstance(part, dict):
            continue
        key = part.get("id") or id(part)
        if event.get("type") == "text" and isinstance(part.get("text"), str):
            # Parts arrive either as growing snapshots or as deltas under the same id.
            seen, text = texts.get(key, ""), part["text"]
            texts[key] = text if text.startswith(seen) else seen if seen.startswith(text) else seen + text
        elif event.get("type") == "tool_use":
            tools.add(key)
    session, deleted = (sessions[0] if sessions else None), False
    if session:
        # --archived only hides the session; delete it so the check leaves nothing in the user's storage.
        try:
            _run(["opencode", "session", "delete", session, "--standalone"], cwd=project, timeout=60, data="",
                 env={**env, "PWD": str(project)})
            deleted = True
        except (subprocess.SubprocessError, OSError):
            pass
    return {"reply": "\n".join(texts.values()), "tool_calls": len(tools), "session": session, "deleted": deleted}


def check_injection(record_path=RECORD, runner=run_model, version_text=None, today=None):
    """Ask each recorded model to quote the subagent model parameter; record the version only if all pass."""
    record = read_record(record_path)
    models = record["models"]
    if len(set(models)) != len(models) or sorted(models) != sorted(APPROVED_MODELS):
        raise ValueError(f"the record's models {models} are not the approved list {list(APPROVED_MODELS)}; "
                         "changing them needs the user's approval")
    notes = note_texts((ROOT / "index.js").read_text())
    if version_text is None:
        version_text = _run(["opencode", "--version"], timeout=30)
    work = Path(tempfile.mkdtemp(prefix="vibe-injection-check-")).resolve()
    results = []
    try:
        project = prepare(work, INJECTION_SETTINGS)
        config_home = work / "config"
        config_home.mkdir()
        for model in record["models"]:
            try:
                answer = runner(project, model, isolated_env(config_home))
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                answer = {"reply": f"run failed: {error}", "tool_calls": 0}
            passed = injection_passes(answer["reply"], answer["tool_calls"], notes)
            results.append({"model": model, "pass": passed, "excerpt": "" if passed else answer["reply"][:200],
                            "session_left": None if answer.get("deleted", True) else answer.get("session")})
        try:
            answer = runner(project, SHELL_PROBE_MODEL, isolated_env(config_home), prompt=SHELL_PROMPT)
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            answer = {"reply": f"run failed: {error}", "tool_calls": 0}
        passed = shell_probe_passes(answer["reply"], answer["tool_calls"])
        results.append({"model": SHELL_PROBE_MODEL, "probe": "shell-note", "pass": passed,
                        "excerpt": "" if passed else answer["reply"][:200],
                        "session_left": None if answer.get("deleted", True) else answer.get("session")})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    version = parse_version(version_text)
    recorded = bool(version) and all(item["pass"] for item in results)
    if recorded:
        record.update(version=version, verified=today or datetime.date.today().isoformat())
        Path(record_path).write_text(json.dumps(record, indent=2) + "\n")
    return {"version": version, "results": results, "recorded": recorded}


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


def _run(argv, cwd=None, timeout=120, data=None, env=None):
    return subprocess.run(argv, cwd=cwd, input=data, capture_output=True, text=True, timeout=timeout,
                          check=True, env=env).stdout


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt", action="append")
    mode.add_argument("--injection", action="store_true", help="check that agents receive the injected notes on "
                      "every recorded model; records the OpenCode version only if all pass (pre-authorized)")
    parser.add_argument("--settings", type=json.loads)
    parser.add_argument("--origin", help="origin URL for gh lookups; use a throwaway or local URL, since the "
                        "session's agent could fetch from or push to it")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if args.injection:
        summary = check_injection()
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0 if summary["recorded"] else 1
    print(json.dumps(check(args.prompt, args.settings, args.origin, args.keep), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
