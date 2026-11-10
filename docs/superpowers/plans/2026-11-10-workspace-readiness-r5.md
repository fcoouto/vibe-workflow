# Workspace readiness R5 — startup warning, hook delivery note, release check

Date: 2026-11-10. Slice R5 of the [workspace readiness plan](2026-11-09-workspace-readiness-and-onboarding.md). Approved by the user, including the startup write below.

## 1. Hook delivery note (the one startup write)

**Why:** hook trust cannot be read from any client listing, so `workspace status` reports it `unknown` and can never exit 0. The only real proof that a client trusts and runs the plugin's hooks is the startup hook running.

- The Codex and Claude Code `SessionStart` path writes `$XDG_STATE_HOME/vibe-workflow/delivered/<client>.json` (default `~/.local/state`): client, package version, SHA-256 of that client's hook definition file (`hooks/codex.json` or `hooks/claude-code.json`), time.
- It writes only when the note is absent, its version or hash differs, or it is older than 24 hours. Enrollment does not matter: trust is per client, not per project.
- Rules: private directory and file (0700/0600, existing directory mode untouched, refuse symlinks and foreign owners, as in `runtime/package_versions.py`), atomic replace, a few hundred bytes, inside the existing deadline and skipped when less than 0.5 s remains. Any failure is silent and leaves the startup output unchanged. No network, subprocess or model call.
- `clients/startup.md` "Runtime limits" changes from "performs no writes" to "performs no writes except this note".
- `workspace status`: the hooks item for a client is `ready` only when its note matches the current package version and hook-definition hash and is at most 7 days old (active sessions rewrite it daily; the cell carries the note's time as its reason, an older or future-dated note gives `unknown`); otherwise `unknown` with reason `start a new session`, or `needs-user` when trust is observably missing (unchanged R1 rule).

## 2. Startup readiness warning

- When `SessionStart` already produces output for an enrolled checkout, it reads `workspace-readiness.json` (written by `workspace record`). If the record lists missing required items for this client or the machine, it appends one line (not on the restricted qualification-only context; only requirement IDs listed in `clients/workspace-requirements.json` are named): what is missing, when it was checked, and the next action (`prepare-my-workspace`).
- It repeats at each session start while the record says so; a fresh `workspace record` after preparation clears it. No record, a corrupt record or an unenrolled checkout: no line. Read-only, bounded, failure leaves output unchanged.
- Combined with the existing drift warning, the startup message stays short.

## 3. Release version check

- `scripts/validate.py --release` fails when the package version equals an existing release tag whose packaged content differs from `HEAD` (what `scripts/package.py --release` ships), and also fails when shipped paths have uncommitted changes. Routine `validate.py` is unchanged, so ordinary PRs need no version bump.
- `docs/development.md` "Create release archives only when requested" gains: run `validate.py --release` first.
- Deviation from the plan's §8 wording (checked on every validation): every PR would otherwise need a version bump while releases are cut only on request.

## Checks

Shared core changes (`scripts/workflow.py`, `runtime/startup.py` or adapters, `scripts/validate.py`): affected modules, `--fast`, and one full run on the final candidate. Tests use temporary state directories and fake hook files; nothing writes to the real home.

## Acceptance

- After a new session in a client whose hooks run, `workspace status` shows that client's hooks `ready`; changing the hook definitions or the package version turns it back to `unknown`.
- Startup never fails or slows past its deadline because of the note or the warning, and writes nothing else.
- An enrolled session with a not-ready record shows one warning line; a ready record or no record shows none.
- `validate.py --release` refuses a same-version release with changed content; plain `validate.py` behaves as before.
