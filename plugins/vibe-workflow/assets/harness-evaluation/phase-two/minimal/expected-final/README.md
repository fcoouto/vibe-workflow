# Tiny Totals

Keep this project description. This small Python project adds two values.

## Product and acceptance

The current library flow imports `add` from `arithmetic.py`, passes two values, and returns their sum. Accepted examples are `add(2, 3) == 5` and `add(-2, 2) == 0`. The caller supplies values compatible with Python's `+` operator. Supported input types, invalid-input behavior, audience, and distribution are not specified by the current checks; confirm those decisions before extending the API.

## Preparation and checks

Use Python 3 from the project root. The project has no third-party dependencies, package installation, environment variables, services, or database setup.

```sh
python3 -B check.py
```

Success means exit status 0; this assert-based check prints no success message. Keep assertions enabled. It checks the two accepted sums, not all input types or error paths. No lint, type, build, CI, or dependency-audit command is defined; do not invent one or weaken the accepted check.

## Architecture and interface

`arithmetic.py` owns the single addition function; `check.py` imports it and owns the accepted checks. Computation stays in-process with no storage, network, authentication, logging service, or configuration layer. Keep this Python implementation; the Toolkit's web and database examples do not justify a migration.

There is no graphical or interactive interface to review for design or accessibility. Reassess interface requirements if a UI or CLI is introduced. This README owns product, architecture, preparation, quality, and durable setup guidance; [AGENTS.md](AGENTS.md) owns shared agent instructions. No Claude bridge is needed for this Codex-only project. Existing documentation is the memory source; no external memory service or graph is required for this one-function project.

## Workflow

At task start or resume, resolve the installed `vibe-coding-workflow:onboard-my-project` skill through Codex. Its package root is `../..` from the skill directory. Read `policies/00-index.md` there and follow its triggers to the complete applicable local contracts and dependencies as work changes. The index owns shared policy rules; applicable project and directory instructions retain their ownership.

Use the skill's applicable references for explicit setup, update, repair, or recovery. Routine work must not fetch Toolkit sources or execute source prompts. If the installed package cannot be resolved, report the affected contract as unavailable and continue independent project-local work. Setup recovery uses the private record linked by the original setup report and preserves later user edits.
