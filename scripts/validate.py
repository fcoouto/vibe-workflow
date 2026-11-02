#!/usr/bin/env python3
"""Check this plugin's metadata, local paths, and instruction budget; no network."""

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def local_file(root, value):
    if not isinstance(value, str) or not value.startswith("./"):
        raise ValueError("Package paths must start with ./")
    path = (root / value).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Referenced file must stay inside the plugin")
    return path


def validate_claude_hooks(root, manifest, root_variable):
    hook_file = local_file(root, manifest["hooks"])
    hooks = read_json(hook_file)["hooks"]
    if set(hooks) != {"SessionStart", "PreToolUse"} or len(hooks["SessionStart"]) != 1 or len(hooks["PreToolUse"]) != 1:
        raise ValueError("Require one Claude startup and guarded file-tool hook group")
    group = hooks["SessionStart"][0]
    if group.get("matcher") != "^(startup|resume|clear|compact|fork)$" or len(group["hooks"]) != 1:
        raise ValueError("Register each supported startup source once")
    handler = group["hooks"][0]
    command = f'python3 -I -B "${{{root_variable}}}/scripts/workflow.py" claude-code SessionStart'
    if handler != {"type": "command", "command": command, "timeout": 5}:
        raise ValueError("Use the bounded, silent native startup command")
    group = hooks["PreToolUse"][0]
    if (group.get("matcher") != "^(Edit|Write|NotebookEdit)$" or len(group.get("hooks", [])) != 1):
        raise ValueError("Register only the supported Claude file-tool guard")
    command = f'python3 -I -B "${{{root_variable}}}/scripts/workflow.py" claude-code PreToolUse'
    if group["hooks"][0] != {"type": "command", "command": command, "timeout": 5}:
        raise ValueError("Use the bounded Claude file-tool runtime command")
    local_file(root, "./clients/claude-code.md")
    local_file(root, "./runtime/adapters/claude_code.py")


def validate_codex_hooks(root, manifest):
    hooks = read_json(local_file(root, manifest["hooks"]))["hooks"]
    events = {
        "SessionStart": ("^(startup|resume|clear|compact|fork)$", 5),
        "PreToolUse": ("^apply_patch$", 5),
        "PostToolUse": ("*", 5),
        "Stop": (None, 5),
        "Interrupt": (None, 1),
    }
    if set(hooks) != set(events):
        raise ValueError("Require the supported Codex hook events exactly once")
    for event, (matcher, timeout) in events.items():
        groups = hooks[event]
        if not isinstance(groups, list) or len(groups) != 1:
            raise ValueError("Register one Codex hook group per event")
        group = groups[0]
        expected_group = {"hooks"} if matcher is None else {"matcher", "hooks"}
        if not isinstance(group, dict) or set(group) != expected_group or group.get("matcher") != matcher:
            raise ValueError("Use the required Codex event matcher")
        command = f'python3 -I -B "${{PLUGIN_ROOT}}/scripts/workflow.py" codex {event}'
        if group["hooks"] != [{"type": "command", "command": command, "timeout": timeout}]:
            raise ValueError("Use the bounded Codex runtime command")


def validate(root=ROOT):
    root = root.resolve(strict=True)
    manifest = read_json(root / ".codex-plugin/plugin.json")
    if (root / "plugin.json").exists():
        raise ValueError("A root manifest hides hooks in the qualified Codex versions")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", manifest["name"]):
        raise ValueError("Use a lowercase, hyphenated plugin name")
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]):
        raise ValueError("Set a numeric major.minor.patch package version")
    if not isinstance(manifest["description"], str) or not manifest["description"].strip():
        raise ValueError("Provide a plugin description")
    extension = manifest["extensions"]["com.openai"]
    onboarding = local_file(root, extension["onboardingSkill"])
    claude = read_json(root / ".claude-plugin/plugin.json")
    if any(claude.get(key) != manifest[key] for key in ("name", "version", "description")):
        raise ValueError("Client manifests must identify the same package version")
    if (root / "hooks/hooks.json").exists():
        raise ValueError("Default hooks would duplicate Claude's explicit registration")
    local_file(root, "./clients/codex.md")
    local_file(root, "./runtime/adapters/codex.py")
    validate_codex_hooks(root, manifest)
    validate_claude_hooks(root, claude, "CLAUDE_PLUGIN_ROOT")
    for path in ("scripts/workflow.py", "runtime/__init__.py", "runtime/startup.py",
                 "runtime/engine.py", "runtime/state.py", "runtime/doctor.py", "runtime/evidence.py",
                 "runtime/adapters/__init__.py", "runtime/adapters/session_start.py",
                 "clients/index.md", "clients/startup.md", "clients/runtime.md", "clients/evidence.md", "policies/00-index.md",
                 "runtime/onboarding.py", "runtime/changes.py", "scripts/onboard_coordinator.py",
                 "runtime/recovery_uses.py", "runtime/resource_accounting.py", "runtime/closure_witness.py",
                 "runtime/review_storage.py", "runtime/screening.py",
                 "runtime/delegation.py", "clients/delegation.md", "runtime/repository_gate.py",
                 "scripts/repository_gate.py", "clients/repository-gate.md",
                 "skills/onboard-my-project/SKILL.md",
                 "skills/onboard-my-project/references/procedure.md",
                 "skills/onboard-my-project/references/project-plan.md",
                 "skills/onboard-my-project/references/apply-and-recovery.md",
                 "skills/onboard-my-project/agents/openai.yaml"):
        local_file(root, "./" + path)
    if manifest.get("skills") != "./skills/":
        raise ValueError("Discover the packaged skills directory")
    skills = sorted((root / "skills").glob("*/SKILL.md"))
    if onboarding not in [path.resolve() for path in skills]:
        raise ValueError("Onboarding must reference a packaged skill")

    counts = {}
    for path in skills:
        text = path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        if len(parts) != 3 or parts[0]:
            raise ValueError(f"Missing skill frontmatter: {path.parent.name}")
        metadata = {}
        for line in parts[1].splitlines():
            key, separator, value = line.partition(":")
            if not separator or key in metadata or not value.strip():
                raise ValueError("Use distinct, nonempty single-line metadata fields")
            metadata[key] = value.strip()
        if set(metadata) != {"name", "description"} or metadata["name"] != path.parent.name:
            raise ValueError("Skill metadata must name its containing directory")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["name"]):
            raise ValueError("Use a lowercase, hyphenated skill name")
        count = len(text.split())
        if count > 150:
            raise ValueError(f"{path.parent.name} has {count} words; limit is 150")
        counts[path.parent.name] = count

    market = read_json(root / ".agents/plugins/marketplace.json")
    entries = [item for item in market["plugins"] if item["name"] == manifest["name"]]
    if len(entries) != 1 or entries[0]["source"] != {"source": "local", "path": "./"}:
        raise ValueError("Local marketplace must reference this package root once")
    return {"package": manifest["name"], "validation": "passed", "skill_words": counts,
            "client_execution": "not_checked"}


if __name__ == "__main__":
    try:
        print(json.dumps(validate(), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Validation failed: {error}", file=sys.stderr)
        sys.exit(1)
