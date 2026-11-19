"""Exact runtime export boundary. Development tools never enter the package."""
RUNTIME = (
    "__init__", "routing", "routing_paths", "routing_observations", "routing_funding",
    "subscription_billing", "selector", "storage", "processes", "continuity",
    "native_catalog", "claude_usage", "opencode_account",
)
FILES = (
    "index.js", "project.js", "onboarding.js", "package.json", "README.md", "LICENSE", "assets/logo.png",
    "skills/vibe-onboard-project/SKILL.md", "skills/vibe-onboard-project/references/procedure.md",
    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-prepare-my-workspace/references/procedure.md",
    "skills/vibe-prepare-my-workspace/scripts/check.py", "skills/vibe-prepare-my-workspace/partners.json",
    "scripts/select.py", "config/routes.json", "config/router.example.json",
    "config/strategy-table.example.json",
    *(f"runtime/{name}.py" for name in RUNTIME),
)
