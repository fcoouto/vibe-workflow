"""Claude Code setup observation, read from the client's own hook event stream.

`claude -p --output-format stream-json --include-hook-events` reports every hook the client ran, with its
name, exit code and output, and an `init` event naming the loaded plugin. That is native evidence independent
of the hooks. Only behavior it actually shows is verified; everything else stays pending with its reason.
"""
import hashlib
import json
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
    """Receipt-time check that every retained proof is exactly the digest of its retained facts."""
    if (not isinstance(facts, dict) or set(facts) != {"init", "hooks", "cases"} or len(facts["hooks"]) > MAX_HOOKS
            or not isinstance(facts["cases"], dict)):
        raise StartupError("Invalid retained Claude Code observation")
    for case, proofs in observations.items():
        if case not in facts["cases"] or proofs != [case_proof(facts, case)]:
            raise StartupError("A retained behavior is not the observed Claude Code fact")
    if bootstrap != [case_proof(facts, "init")]:
        raise StartupError("Retained bootstrap evidence is not the observed Claude Code fact")


def issue_witness(*, binding, executable, package, facts):
    """Only this adapter issues a genuine Claude setup witness, from facts it observed."""
    from runtime.doctor import NativeSetupWitness, _NATIVE_WITNESS_ISSUER
    observations = {case: [case_proof(facts, case)] for case in facts["cases"]}
    content = dict(binding=binding,
                   native=dict(surface=SURFACE, version=facts["init"]["claude_version"], executable=str(executable)),
                   package_root=str(Path(package).resolve()), bootstrap=[case_proof(facts, "init")],
                   observations=observations, claude_observation=facts,
                   pending_reason="Behaviors beyond startup context need managed qualification attempts")
    return NativeSetupWitness(content, _issuer=_NATIVE_WITNESS_ISSUER)


def run_observation(executable, package, checkout, *, budget_usd="0.25", timeout=120):
    """One capped, non-interactive run in a disposable enrolled checkout; returns the raw event stream."""
    command = [str(executable), "-p", PROMPT, "--plugin-dir", str(package), "--model", "haiku",
               "--setting-sources", "project", "--max-budget-usd", budget_usd,
               "--output-format", "stream-json", "--include-hook-events", "--verbose"]
    result = subprocess.run(command, cwd=str(checkout), capture_output=True, stdin=subprocess.DEVNULL,
                            timeout=timeout, check=False)
    if result.returncode != 0 or len(result.stdout) > MAX_STREAM:
        raise StartupError("The Claude Code observation run failed or exceeded its bound")
    return result.stdout


def observe(executable, package, checkout, authority, binding):
    """Run once, observe the stream and issue the witness; the caller supplies its own assessment."""
    deadline = time.monotonic() + 150
    raw = run_observation(executable, package, checkout)
    if time.monotonic() > deadline:
        raise StartupError("The Claude Code observation exceeded its deadline")
    facts = observe_stream(raw, package=package, checkout=checkout, authority=authority)
    return issue_witness(binding=binding, executable=executable, package=package, facts=facts), raw
