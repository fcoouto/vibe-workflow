# Engineering

Run python3 -B check.py. Python standard library only.
Keep the root AGENTS.md and src/AGENTS.md as the instruction owners.

## Preparation and accepted proof

Use Python 3 from the repository root. There are no third-party dependencies, installation steps, environment variables, or services to prepare.

```sh
python3 -B check.py
```

Success means exit status 0; this assert-based check prints no success message. Keep assertions enabled. It checks trimming, order, duplicates, and one Unicode example. It does not define all input-type or error behavior. No lint, type, build, CI, or dependency-audit command is defined; preserve the existing checks and report missing proof when new requirements need it.

This handbook remains canonical: [overview.md](overview.md) owns product and acceptance; [ARCHITECTURE.md](ARCHITECTURE.md) owns structure; this file owns preparation, quality, and durable workflow guidance. Read [root instructions](../AGENTS.md) and, for source work, [source instructions](../src/AGENTS.md). No Claude bridge is needed for this Codex-only project. Existing versioned guidance is the memory source; this one-function module needs no external memory service or graph. Keep transient task and setup evidence in the existing task context and private setup record.

## Workflow

At task start or resume, resolve the installed `vibe-coding-workflow:onboard-my-project` skill through Codex. Its package root is `../..` from the skill directory. Read `policies/00-index.md` there and follow its triggers to the complete applicable local contracts and dependencies as work changes. The index owns shared policy rules; applicable project and directory instructions retain their ownership.

Use the skill's applicable references for explicit setup, update, repair, or recovery. Routine work must not fetch Toolkit sources or execute source prompts. If the installed package cannot be resolved, report the affected contract as unavailable and continue independent project-local work. Setup recovery uses the private record linked by the original setup report and preserves later user edits.

Owner note added after setup: preserve the Friday-release convention.
