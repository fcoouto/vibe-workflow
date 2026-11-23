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
import unicodedata  # noqa: E402
import os  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = 'id: "vibe-workflow",'
LIVE_ID = 'id: "vibe-workflow-live",'
RECORD = ROOT / "tests" / "tooling" / "opencode-verified.json"
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
# The three subscription paths Smart Router routes, provider -> harness. The record holds one model per path; when one
# is no longer listed, the user pre-authorized one level-1 Smart Router pick on that path (council 51429ec7c824).
ROUTES = ROOT / "config" / "routes.json"
# Exactly the paths the user authorized; a new or re-billed route in routes.json does not extend the authorization.
AUTHORIZED = ("claude-agent", "openai", "opencode-go")


def authorized_paths(config):
    """provider -> harness for the authorized subscription paths; anything else in the config refuses the check."""
    rules = config.get("paths") if isinstance(config, dict) else None
    paths = {rule.get("provider"): rule.get("harness") for rule in rules or [] if isinstance(rule, dict)}
    if set(paths) != set(AUTHORIZED) or len(paths) != len(rules or []) or any(
            rule.get("billing_class") != "included_subscription" for rule in rules or []):
        raise ValueError(f"config/routes.json must hold exactly the authorized subscription paths {list(AUTHORIZED)}, "
                         "each billed as included_subscription; changing them needs the user's approval")
    return paths


PATHS = {rule["provider"]: rule["harness"] for rule in json.loads(ROUTES.read_text())["paths"]}
LEVEL_ONE = {"v": 2, "work_kind": "testing", "task": {"files": "1-2", "spec": "exact", "risk": "reversible"}}
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "vibe-workflow" / "smart-router"
INJECTION_SETTINGS = {"version": 1, "features": [{"smart_router": {"enabled": True}},
                                                {"git_workflow": {"enabled": True}}]}
# One shell-note probe, on the claude-agent path's model, proves the execute.after hook still appends its notes to
# tool results: a branch switch in the clone (a primary checkout) must come back with the primary-checkout note.
SHELL_PROBE_PATH = "claude-agent"
SHELL_COMMAND = "git switch -c vibe-injection-probe"
SHELL_PROMPT = ("Run exactly this one shell command, and no other tool call:\n" + SHELL_COMMAND + "\n"
                "Then quote verbatim every line of that command's result that starts with vibe-workflow:")
# OpenCode V2 permissions: the last matching rule wins, so the one allowed command follows the blanket deny of every
# action. A model that calls any other tool or command is refused before it runs, instead of being caught by the
# tool-call count afterwards.
SHELL_PERMISSIONS = [{"action": "*", "resource": "*", "effect": "deny"},
                     {"action": "shell", "resource": SHELL_COMMAND, "effect": "allow"}]
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


def shell_probe_passes(answer):
    """The hook delivered the note (it is in the one exact command's result) and the model saw it (it quotes it)."""
    tools = answer.get("tools") or []
    if answer.get("tool_calls") != 1 or len(tools) != 1 or (tools[0].get("command") or "").strip() != SHELL_COMMAND:
        return False
    return all(SHELL_NOTE.search(normalize(text)) for text in (tools[0].get("output"), answer.get("reply")))


def isolated_env(config_home):
    """The user's logins stay (HOME, data dirs); their OpenCode config, and so their installed plugins, do not.

    OPENCODE_* variables can point at another config (OPENCODE_CONFIG, _DIR, _CONTENT) or the caller's session.
    """
    # GIT_* variables could point Git somewhere other than the throwaway clone.
    env = {name: value for name, value in os.environ.items() if not name.startswith(("OPENCODE", "GIT_"))}
    return {**env, "XDG_CONFIG_HOME": str(config_home)}


def shell_probe_env(work, project):
    """The probe's environment: a config home of its own, holding only the shell rules (the note probes keep an empty one)."""
    config_home = work / "shell-config"
    (config_home / "opencode").mkdir(parents=True)
    (config_home / "opencode" / "opencode.json").write_text(
        json.dumps({"$schema": "https://opencode.ai/config.json", "permissions": SHELL_PERMISSIONS}, indent=2) + "\n")
    return {**isolated_env(config_home), "PWD": str(project)}


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
    texts, tools, sessions = {}, {}, []
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
        elif event.get("type") == "tool_use" and key not in tools:
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            given = state.get("input") if isinstance(state.get("input"), dict) else {}
            content = (state.get("metadata") or {}).get("content") if isinstance(state.get("metadata"), dict) else None
            texts_out = [item.get("text", "") for item in content if isinstance(item, dict)] if isinstance(content, list) else []
            if isinstance(state.get("output"), str):
                texts_out.append(state["output"])
            tools[key] = {"command": given.get("command") if isinstance(given.get("command"), str) else None,
                          "output": "\n".join(text for text in texts_out if isinstance(text, str))}
    session, deleted = (sessions[0] if sessions else None), False
    if session:
        # --archived only hides the session; delete it so the check leaves nothing in the user's storage.
        try:
            _run(["opencode", "session", "delete", session, "--standalone"], cwd=project, timeout=60, data="",
                 env={**env, "PWD": str(project)})
            deleted = True
        except (subprocess.SubprocessError, OSError):
            pass
    return {"reply": "\n".join(texts.values()), "tool_calls": len(tools), "tools": list(tools.values()),
            "session": session, "deleted": deleted}


def _host_list(raw):
    items = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("OpenCode's model list has an unexpected shape")
    return items


def reduce_models(raw):
    """OpenCode's model list reduced exactly as index.js models() reduces ctx.model.list() for the selector."""
    def text(value):
        # index.js: 1..256 characters, and no control, format or separator character other than a space.
        return isinstance(value, str) and 0 < len(value) <= 256 and not any(
            unicodedata.category(char)[0] in "CZ" for char in value.replace(" ", ""))

    def amount(value):
        return value if type(value) is int and 0 <= value <= 2 ** 53 - 1 else None
    found = []
    for item in _host_list(raw):
        if not isinstance(item, dict) or not item.get("enabled") or item.get("status") == "deprecated" \
                or (item.get("capabilities") or {}).get("tools") is False or item.get("providerID") not in PATHS \
                or not text(item.get("id")):
            continue
        released = amount((item.get("time") or {}).get("released"))
        variants = item.get("variants") if isinstance(item.get("variants"), list) else []
        found.append({"provider": item["providerID"], "id": item["id"],
                      "model": item["modelID"] if text(item.get("modelID")) else None,
                      "family": item["family"] if text(item.get("family")) else None,
                      "variants": [v.get("id") for v in variants if isinstance(v, dict) and text(v.get("id"))][:32],
                      "context": amount((item.get("limit") or {}).get("context")),
                      "released": None if released is None else released // 1000})
        if len(found) == 512:
            break
    return found


def read_host_models(env):
    """OpenCode's raw model list without a model call, from a private server in the clone (env carries PWD)."""
    out = _run(["opencode", "api", "get", "/api/model", "--standalone"], cwd=env["PWD"], timeout=120, data="", env=env)
    return json.loads(out)


def pick_for_path(provider, models):
    """One level-1 Smart Router pick on one path: (provider/id, None, paid_overflow) or (None, reason, None)."""
    request = {**LEVEL_ONE, "models": models, "constraints": {"allowed_harnesses": [PATHS[provider]]}}
    try:
        answer = json.loads(_run([sys.executable, "-I", "-B", str(ROOT / "scripts" / "select.py"), "--state-dir",
                                  str(STATE_DIR)], timeout=120, data=json.dumps(request)))
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        return None, f"selector failed: {error}", None
    if not isinstance(answer, dict):
        return None, "selector answered an unexpected shape", None
    route = answer.get("route")
    if answer.get("state") != "selected":
        return None, answer.get("reason") if isinstance(answer.get("reason"), str) else "no_selection", None
    if not isinstance(route, dict) or not all(isinstance(route.get(key), str) for key in ("provider", "model")):
        return None, "selector answered an unexpected route", None
    return f"{route['provider']}/{route['model']}", None, route.get("paid_overflow")


def _models_by_path(models):
    by_path = {}
    for model in models:
        provider = model.split("/", 1)[0]
        if provider not in PATHS or provider in by_path:
            raise ValueError(f"the record needs one model per subscription path ({sorted(PATHS)}), got {models}; "
                             "changing that needs the user's approval")
        by_path[provider] = model
    if set(by_path) != set(PATHS):
        raise ValueError(f"the record needs one model per subscription path ({sorted(PATHS)}), got {models}")
    return by_path


def _answer(runner, project, model, env, **prompt):
    try:
        return runner(project, model, env, **prompt)
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        return {"reply": f"run failed: {error}", "tool_calls": 0}


def check_injection(record_path=RECORD, runner=run_model, version_text=None, today=None, host_models=None,
                    picker=None):
    """Ask one model per subscription path to quote the subagent model parameter, then run the shell-note probe.

    A recorded model that is no longer listed is replaced by one level-1 Smart Router pick on its path. The record is
    written (version, date and the models used) only when every pick and every probe passes.
    """
    record = read_record(record_path)
    authorized_paths(json.loads(ROUTES.read_text()))
    if set(PATHS) != set(AUTHORIZED):
        raise ValueError(f"only the authorized subscription paths {list(AUTHORIZED)} may run")
    by_path = _models_by_path(record["models"])
    notes = note_texts((ROOT / "index.js").read_text())
    host_models, picker = host_models or read_host_models, picker or pick_for_path
    if version_text is None:
        version_text = _run(["opencode", "--version"], timeout=30)
    work = Path(tempfile.mkdtemp(prefix="vibe-injection-check-")).resolve()
    results, chosen = [], {}
    try:
        project = prepare(work, INJECTION_SETTINGS)
        config_home = work / "config"
        config_home.mkdir()
        env = {**isolated_env(config_home), "PWD": str(project)}
        try:
            raw = _host_list(host_models(env))
            if not raw:
                raise ValueError("OpenCode listed no models")
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            raw = None
            results.append({"path": None, "model": None, "outcome": "models_unavailable", "pass": False,
                            "excerpt": str(error)[:200]})
        if raw is not None:
            listed = {f"{item.get('providerID')}/{item.get('id')}" for item in raw if isinstance(item, dict)}
            eligible_models = reduce_models(raw)
            eligible = {f"{item['provider']}/{item['id']}" for item in eligible_models}
            for provider, model in by_path.items():
                if model in eligible:
                    chosen[provider] = (model, None, None)
                    continue
                if model in listed:
                    # Still listed but filtered out (disabled, deprecated, no tools): not retired, so no pick.
                    results.append({"path": provider, "model": model, "outcome": "could_not_select", "pass": False,
                                    "excerpt": "the recorded model is listed but not eligible; ask the user"})
                    continue
                pick, reason, overflow = picker(provider, eligible_models)
                if pick and pick.split("/", 1)[0] == provider and pick in eligible:
                    chosen[provider] = (pick, model, overflow)
                else:
                    results.append({"path": provider, "model": model, "outcome": "could_not_select", "pass": False,
                                    "excerpt": reason or f"pick {pick} is not an eligible model on {provider}"})
        if not results:
            for provider, (model, replaced, overflow) in chosen.items():
                answer = _answer(runner, project, model, env)
                passed = injection_passes(answer["reply"], answer["tool_calls"], notes)
                results.append({"path": provider, "model": model, "replaced": replaced, "paid_overflow": overflow,
                                "pass": passed,
                                "outcome": "delivered" if passed else "not_delivered",
                                "excerpt": "" if passed else answer["reply"][:200],
                                "session_left": None if answer.get("deleted", True) else answer.get("session")})
            model = chosen[SHELL_PROBE_PATH][0]
            answer = _answer(runner, project, model, shell_probe_env(work, project), prompt=SHELL_PROMPT)
            passed = shell_probe_passes(answer)
            results.append({"path": SHELL_PROBE_PATH, "model": model, "probe": "shell-note", "pass": passed,
                            "outcome": "delivered" if passed else "not_delivered",
                            "excerpt": "" if passed else answer["reply"][:200],
                            "session_left": None if answer.get("deleted", True) else answer.get("session")})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    version = parse_version(version_text)
    recorded = bool(version) and bool(results) and all(item["pass"] for item in results)
    if recorded:
        record.update(models=[chosen[provider][0] for provider in by_path], version=version,
                      verified=today or datetime.date.today().isoformat())
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
    # Inherited GIT_* variables (GIT_DIR, GIT_WORK_TREE, ...) would point Git away from the paths given here.
    if env is None:
        env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
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
    # The session may run Git in the clone; no global or repository hook may run there.
    _run(["git", "config", "core.hooksPath", "/dev/null"], cwd=project)
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
