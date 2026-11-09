"""Claude Code setup observation, read from the client's own hook event stream.

`claude -p --output-format stream-json --include-hook-events` reports every hook the client ran, with its
name, exit code and output, and an `init` event naming the loaded plugin. That is native evidence independent
of the hooks. Only behavior it actually shows is verified; everything else stays pending with its reason.
"""
import hashlib
import json
import os
import stat
import subprocess
import time
from pathlib import Path

from runtime.startup import StartupError, json_object


SURFACE = "claude-code-cli"
MAX_STREAM = 4 * 1024 * 1024
MAX_ROWS = 4096
MAX_HOOKS = 256
PROMPT = "Use the Read tool on README.md if it exists, then reply with the single word ok."


def _digest(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_stream(raw):
    """Bounded stream-json rows; any malformed row invalidates the whole observation."""
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_STREAM:
        raise StartupError("Claude Code event stream is missing or exceeds its bound")
    lines = bytes(raw).splitlines()
    if len(lines) > MAX_ROWS:
        raise StartupError("Claude Code event stream has too many rows")
    try:
        return [json_object(line, limit=MAX_STREAM) for line in lines if line.strip()]
    except (ValueError, RecursionError, UnicodeError):
        raise StartupError("Claude Code event stream contains a malformed row") from None


def _text(value, limit=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise StartupError("Invalid Claude Code observation text")
    return value


def observe_stream(raw, *, package, checkout, authority):
    """Facts proven by one real run: plugin identity, every hook outcome and the startup context.

    The run must have loaded exactly this package and version in exactly this checkout, and every hook it
    executed must have succeeded. The startup context must name this package's policy index and this
    checkout's accepted authority, which ties the observed output to the enrolled project.
    """
    rows = parse_stream(raw)
    package, checkout, authority = Path(package).resolve(), Path(checkout).resolve(), Path(authority)
    inits = [row for row in rows if row.get("type") == "system" and row.get("subtype") == "init"]
    if len(inits) != 1:
        raise StartupError("Claude Code stream lacks exactly one init event")
    init = inits[0]
    manifest = json_object((package / ".claude-plugin/plugin.json").read_bytes())
    loaded = [item for item in init.get("plugins", []) if isinstance(item, dict) and item.get("name") == manifest["name"]]
    if (len(loaded) != 1 or not isinstance(loaded[0].get("path"), str) or Path(loaded[0]["path"]).resolve() != package
            or loaded[0].get("version") != manifest["version"]):
        raise StartupError("The observed run did not load this exact package and version")
    if not isinstance(init.get("cwd"), str) or Path(init["cwd"]).resolve() != checkout:
        raise StartupError("The observed run used another checkout")
    version = _text(init.get("claude_code_version"), 128)
    hooks = []
    for row in rows:
        if row.get("type") != "system" or row.get("subtype") != "hook_response":
            continue
        if row.get("exit_code") != 0 or row.get("outcome") != "success":
            raise StartupError("A packaged hook failed during the observed run")
        stdout = row.get("stdout", "")
        if not isinstance(stdout, str):
            raise StartupError("Malformed hook output in the observed run")
        hooks.append(dict(event=_text(row.get("hook_event"), 64), name=_text(row.get("hook_name"), 128),
                          outcome="success", exit_code=0, stdout_bytes=len(stdout.encode("utf-8")),
                          stdout_sha256=hashlib.sha256(stdout.encode("utf-8")).hexdigest()))
        if len(hooks) > MAX_HOOKS:
            raise StartupError("Too many hook results in the observed run")
        if row["hook_event"] == "SessionStart":
            hooks[-1]["_stdout"] = stdout
    if sum(1 for hook in hooks if hook["event"] == "SessionStart") != 1:
        raise StartupError("The observed run must show exactly one SessionStart hook")
    facts = dict(init=dict(claude_version=version, session_id=_text(init.get("session_id"), 128), cwd=str(checkout),
                           plugin=dict(name=manifest["name"], path=str(package), version=manifest["version"],
                                       source=_text(loaded[0].get("source"), 256)),
                           setting_sources=["project"]),
                 hooks=[], cases={})
    for index, hook in enumerate(hooks):
        stdout = hook.pop("_stdout", None)
        facts["hooks"].append(hook)
        if stdout is None or "startup-context" in facts["cases"]:
            continue
        try:
            context = json_object(stdout.encode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        except (StartupError, KeyError, TypeError):
            continue
        if (isinstance(context, str) and json.dumps(str(package / "policies/00-index.md")) in context
                and json.dumps(str(authority)) in context):
            facts["cases"]["startup-context"] = dict(hook_index=index, context_sha256=hashlib.sha256(
                context.encode("utf-8")).hexdigest(), names_policy_index=True, names_authority=True)
    return facts


def case_proof(facts, case):
    """The retained evidence reference for one observed case; recomputed from the retained facts."""
    if case == "init":
        material = facts["init"]
    else:
        observed = facts["cases"][case]
        material = dict(init=facts["init"], hook=facts["hooks"][observed["hook_index"]], case=observed)
    return dict(reference=f"claude-observation:{facts['init']['session_id']}#{case}", sha256=_digest(material))


def verify_facts(facts, observations, bootstrap):
    """Receipt-time integrity check: each proof is the digest of its retained facts, and the facts are shaped.

    This shows the retained facts are self-consistent and well-formed; it does not authenticate them.
    """
    try:
        _verify_facts(facts, observations, bootstrap)
    except (LookupError, TypeError, AttributeError):
        raise StartupError("Invalid retained Claude Code observation") from None


def _verify_facts(facts, observations, bootstrap):
    if (not isinstance(facts, dict) or set(facts) != {"init", "hooks", "cases"} or not isinstance(facts["hooks"], list)
            or len(facts["hooks"]) > MAX_HOOKS or not isinstance(facts["cases"], dict) or set(facts["cases"]) - {"startup-context"}):
        raise StartupError("Invalid retained Claude Code observation")
    for case, observed in facts["cases"].items():
        index = observed["hook_index"]
        if (type(index) is not int or not 0 <= index < len(facts["hooks"]) or facts["hooks"][index]["event"] != "SessionStart"
                or facts["hooks"][index]["exit_code"] != 0 or observed["names_policy_index"] is not True
                or observed["names_authority"] is not True or not isinstance(observed["context_sha256"], str)):
            raise StartupError("A retained behavior does not point at a successful SessionStart hook")
    for case, proofs in observations.items():
        if case not in facts["cases"] or proofs != [case_proof(facts, case)]:
            raise StartupError("A retained behavior is not the observed Claude Code fact")
    if bootstrap != [case_proof(facts, "init")]:
        raise StartupError("Retained bootstrap evidence is not the observed Claude Code fact")


def issue_witness(*, binding, executable, package, raw, checkout, authority):
    """Only this adapter issues a genuine Claude setup witness, and only from a raw stream it parses itself."""
    from runtime.doctor import NativeSetupWitness, _NATIVE_WITNESS_ISSUER
    facts = observe_stream(raw, package=package, checkout=checkout, authority=authority)
    observations = {case: [case_proof(facts, case)] for case in facts["cases"]}
    content = dict(binding=binding,
                   native=dict(surface=SURFACE, version=facts["init"]["claude_version"], executable=str(executable)),
                   package_root=str(Path(package).resolve()), bootstrap=[case_proof(facts, "init")],
                   observations=observations, claude_observation=facts,
                   pending_reason="Behaviors beyond startup context need managed qualification attempts")
    return NativeSetupWitness(content, _issuer=_NATIVE_WITNESS_ISSUER)


def _refuse_project_hooks(checkout):
    """Project hooks would run during the observation itself, so they are refused before `claude` starts."""
    for name in ("settings.json", "settings.local.json"):
        try:
            descriptor = os.open(Path(checkout) / ".claude" / name, os.O_RDONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        except OSError:
            raise StartupError("A project settings file is unreadable, so its hooks cannot be ruled out") from None
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise StartupError("A project settings file is not a regular file")
            try:
                value = json.loads(handle.read(256 * 1024 + 1))
            except (ValueError, UnicodeError, RecursionError):
                raise StartupError("A project settings file is unreadable, so its hooks cannot be ruled out") from None
        if not isinstance(value, dict) or "hooks" in value:
            raise StartupError("Project hooks could run during the observation; refuse before starting Claude Code")


def run_observation(executable, package, checkout, *, budget_usd="0.25", timeout=120):
    """One capped, non-interactive run in a disposable enrolled checkout; returns the raw event stream."""
    _refuse_project_hooks(checkout)
    command = [str(executable), "-p", PROMPT, "--plugin-dir", str(package), "--model", "haiku",
               "--setting-sources", "project", "--max-budget-usd", budget_usd,
               "--output-format", "stream-json", "--include-hook-events", "--verbose"]
    try:
        result = subprocess.run(command, cwd=str(checkout), capture_output=True, stdin=subprocess.DEVNULL,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise StartupError("The Claude Code observation run could not complete") from None
    if result.returncode != 0 or len(result.stdout) > MAX_STREAM:
        raise StartupError("The Claude Code observation run failed or exceeded its bound")
    return result.stdout


def observe(executable, package, checkout, authority, binding):
    """Run once, observe the stream and issue the witness; the caller supplies its own assessment."""
    deadline = time.monotonic() + 150
    raw = run_observation(executable, package, checkout)
    if time.monotonic() > deadline:
        raise StartupError("The Claude Code observation exceeded its deadline")
    return issue_witness(binding=binding, executable=executable, package=package, raw=raw,
                         checkout=checkout, authority=authority), raw
