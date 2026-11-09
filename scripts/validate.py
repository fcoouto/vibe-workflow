#!/usr/bin/env python3
"""Check this plugin's metadata, local paths, and instruction budget; no network."""

import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Long files cost agents context to read. Existing runtime modules keep their
# current size as a ceiling until they are split; do not add entries or raise them.
MAX_PYTHON_LINES = 1000
SKIPPED_DIRECTORIES = {"__pycache__", "node_modules", "venv", "site-packages"}
OVERSIZED_PYTHON = {
    "runtime/adapters/codex.py": 1579,
    "runtime/changes.py": 6074,
    "runtime/delegation.py": 3369,
    "runtime/evidence.py": 8505,
    "runtime/onboarding.py": 2620,
    "runtime/review_storage.py": 1297,
    "runtime/routing.py": 1506,
    "runtime/setup_observation.py": 1526,
    "runtime/startup.py": 1515,
    "runtime/state.py": 2613,
    "runtime/usage_budget.py": 1616,
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def local_file(root, value):
    if not isinstance(value, str) or not value.startswith("./"):
        raise ValueError("Package paths must start with ./")
    path = (root / value).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Referenced file must stay inside the plugin")
    return path


def validate_receiver_patch(root, name, provenance):
    patch = local_file(root, f"./assets/habblaud/{name}.patch").read_bytes()
    if provenance.get("patch_sha256") != hashlib.sha256(patch).hexdigest():
        raise ValueError(f"Bundled {name} patch digest differs from its provenance")
    files = re.findall(r"^diff --git a/(\S+) b/\S+$", patch.decode("utf-8"), re.MULTILINE)
    if not files or provenance.get("files") != files:
        raise ValueError(f"Bundled {name} patch inventory differs from its provenance")


def validate_startup_hook(root, manifest, client, root_variable):
    hook_file = local_file(root, manifest["hooks"])
    hooks = read_json(hook_file)["hooks"]
    if set(hooks) != {"SessionStart"} or len(hooks["SessionStart"]) != 1:
        raise ValueError("Require one startup hook group per client")
    group = hooks["SessionStart"][0]
    if group.get("matcher") != "^(startup|resume|clear|compact|fork)$" or len(group["hooks"]) != 1:
        raise ValueError("Register each supported startup source once")
    handler = group["hooks"][0]
    command = f'python3 -I -B "${{{root_variable}}}/scripts/workflow.py" {client} SessionStart'
    if handler != {"type": "command", "command": command, "timeout": 5}:
        raise ValueError("Use the bounded, silent native startup command")
    local_file(root, "./clients/" + client + ".md")
    local_file(root, "./runtime/adapters/" + client.replace("-", "_") + ".py")


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
    from scripts.read_instructions import build_manifest
    local_file(root, "./scripts/read_instructions.py")
    local_file(root, "./scripts/drain_instructions.js")
    if read_json(local_file(root, "./clients/instruction-map.json")) != build_manifest(root):
        raise ValueError("Prepared instruction map is stale; run scripts/read_instructions.py --prepare during authoring")
    validate_codex_hooks(root, manifest)
    validate_startup_hook(root, claude, "claude-code", "CLAUDE_PLUGIN_ROOT")
    receiver = read_json(local_file(root, "./assets/habblaud/receiver.json"))
    local_file(root, "./assets/habblaud/LICENSE")
    upgrade = receiver.get("upgrade")
    if (receiver.get("version") != 1 or not isinstance(upgrade, dict)
            or not re.fullmatch(r"[a-f0-9]{64}", str(upgrade.get("from_patch_sha256")))
            or upgrade["from_patch_sha256"] == receiver.get("patch_sha256")):
        raise ValueError("Invalid receiver upgrade provenance")
    validate_receiver_patch(root, "receiver", receiver)
    validate_receiver_patch(root, "receiver-upgrade", upgrade)
    for path in ("scripts/workflow.py", "runtime/__init__.py", "runtime/startup.py",
                 "runtime/engine.py", "runtime/state.py", "runtime/doctor.py", "runtime/evidence.py",
                 "runtime/adapters/__init__.py", "runtime/adapters/session_start.py",
                 "clients/index.md", "clients/startup.md", "clients/runtime.md", "clients/evidence.md", "policies/00-index.md",
                 "runtime/onboarding.py", "runtime/changes.py", "scripts/onboard_coordinator.py",
                 "runtime/recovery_uses.py", "runtime/resource_accounting.py", "runtime/closure_witness.py",
                 "runtime/review_storage.py", "runtime/screening.py",
                 "runtime/delegation.py", "clients/delegation.md", "runtime/repository_gate.py",
                 "runtime/worker_visibility.py", "runtime/habblaud.py", "scripts/worker_visibility.py", "clients/worker-visibility.md",
                 "clients/habblaud-receiver.md",
                 "scripts/repository_gate.py", "clients/repository-gate.md",
                 "skills/onboard-my-project/SKILL.md",
                 "skills/onboard-my-project/references/procedure.md",
                 "skills/onboard-my-project/references/apply-and-recovery.md",
                 "skills/onboard-my-project/agents/openai.yaml",
                 "clients/harness-evaluation.md", "scripts/validate-harness.mjs",
                 "assets/harness-evaluation/LICENSE", "assets/harness-evaluation/cases.md",
                 "assets/harness-evaluation/boundary/AGENTS.md",
                 "assets/harness-evaluation/boundary/access.mjs",
                 "assets/harness-evaluation/boundary/local-notes.txt",
                 "assets/harness-evaluation/phase-two/minimal/input/README.md",
                 "assets/harness-evaluation/phase-two/minimal/input/AGENTS.md",
                 "assets/harness-evaluation/phase-two/minimal/input/arithmetic.py",
                 "assets/harness-evaluation/phase-two/minimal/input/check.py",
                 "assets/harness-evaluation/phase-two/minimal/expected-final/README.md",
                 "assets/harness-evaluation/phase-two/minimal/expected-final/AGENTS.md",
                 "assets/harness-evaluation/phase-two/customized/input/README.md",
                 "assets/harness-evaluation/phase-two/customized/input/AGENTS.md",
                 "assets/harness-evaluation/phase-two/customized/input/check.py",
                 "assets/harness-evaluation/phase-two/customized/input/handbook/overview.md",
                 "assets/harness-evaluation/phase-two/customized/input/handbook/engineering.md",
                 "assets/harness-evaluation/phase-two/customized/input/handbook/ARCHITECTURE.md",
                 "assets/harness-evaluation/phase-two/customized/input/src/AGENTS.md",
                 "assets/harness-evaluation/phase-two/customized/input/src/cleaning.py",
                 "assets/harness-evaluation/phase-two/customized/expected-after-partial-undo/handbook/engineering.md"):
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

    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part in SKIPPED_DIRECTORIES for part in relative.parts):
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        limit = OVERSIZED_PYTHON.get(relative.as_posix(), MAX_PYTHON_LINES)
        if lines > limit:
            raise ValueError(f"{relative} has {lines} lines; limit is {limit}")

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
