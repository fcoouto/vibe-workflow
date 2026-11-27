"""Exact runtime export boundary. Development tools never enter the package."""
RUNTIME = (
    "__init__", "routing", "routing_paths", "routing_observations",
    "subscription_billing", "selector", "storage", "processes", "continuity",
    "native_catalog", "claude_usage", "claude_quota", "opencode_account", "scores", "codex_reset", "chatgpt_limit",
    "council", "feedback", "usage_balance", "private_folder",
)
FILES = (
    "index.js", "src/project.js", "src/onboarding.js", "src/pr-advice.js", "src/advice.js", "src/skill-advice.js", "src/merged-worktrees.js", "src/session-facts.js", "src/session-titles.js", "src/council.js",
    "src/claude-limit-events.js", "package.json", "README.md", "LICENSE", "assets/logo.png",
    "skills/vibe-onboard-project/SKILL.md", "skills/vibe-onboard-project/references/procedure.md",
    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-prepare-my-workspace/references/procedure.md",
    "skills/vibe-prepare-my-workspace/scripts/check.py", "skills/vibe-prepare-my-workspace/partners.json",
    "skills/vibe-council/SKILL.md", "skills/vibe-council/references/procedure.md",
    "skills/vibe-council/references/brief-template.md",
    "skills/vibe-council/references/record-template.md",
    "skills/vibe-project-layout/SKILL.md", "skills/vibe-project-layout/references/procedure.md",
    "skills/vibe-project-layout/references/layouts.md",
    "skills/vibe-pull-requests/SKILL.md", "skills/vibe-pull-requests/references/procedure.md",
    "skills/vibe-pull-requests/references/pull-request-template.md",
    "skills/vibe-pull-requests/references/worktree-cleanup.md", "skills/vibe-pull-requests/scripts/archive.py",
    "skills/vibe-security-review/SKILL.md", "skills/vibe-security-review/references/ship-check.md",
    "skills/vibe-security-review/references/full-review.md", "skills/vibe-security-review/references/reviewer-brief.md",
    "skills/vibe-security-review/references/report-storage.md",
    "skills/vibe-security-review/scripts/store_report.py",
    "scripts/select.py", "config/routes.json",
    *(f"runtime/{name}.py" for name in RUNTIME),
)
