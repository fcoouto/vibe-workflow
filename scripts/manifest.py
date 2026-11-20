"""Exact runtime export boundary. Development tools never enter the package."""
RUNTIME = (
    "__init__", "routing", "routing_paths", "routing_observations",
    "subscription_billing", "selector", "storage", "processes", "continuity",
    "native_catalog", "claude_usage", "opencode_account", "scores", "codex_reset", "chatgpt_limit",
    "council",
)
FILES = (
    "index.js", "project.js", "onboarding.js", "pr-advice.js", "session-facts.js", "session-titles.js",
    "package.json", "README.md", "LICENSE", "assets/logo.png",
    "skills/vibe-onboard-project/SKILL.md", "skills/vibe-onboard-project/references/procedure.md",
    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-prepare-my-workspace/references/procedure.md",
    "skills/vibe-prepare-my-workspace/scripts/check.py", "skills/vibe-prepare-my-workspace/partners.json",
    "skills/vibe-council/SKILL.md", "skills/vibe-council/references/procedure.md",
    "skills/vibe-council/references/brief-template.md",
    "skills/vibe-council/references/record-template.md",
    "skills/vibe-pull-requests/SKILL.md", "skills/vibe-pull-requests/references/procedure.md",
    "skills/vibe-pull-requests/references/pull-request-template.md",
    "skills/vibe-pull-requests/references/worktree-cleanup.md",
    "scripts/select.py", "config/routes.json",
    *(f"runtime/{name}.py" for name in RUNTIME),
)
