# Setup record

Project: `vibe-workflow` (this repository is the plugin itself; enrolled by explicit user selection on 2026-11-08). Clients: Codex and Claude Code. Shape: standalone repository, remote `fcoouto/vibe-workflow`, primary branch `main`.

## Source

Vibe Coding Toolkit `soumatheusgomes/vibe-coding-toolkit` at `21b71b68679c11184f49c23168852014bf6dde8b` (online, 2026-11-08). Setup source only: nothing here fetches or runs Toolkit content.

## Decisions

| Choice | Answer | Provenance |
| --- | --- | --- |
| Publishing | Commit, push a task branch, open a draft PR | User, 2026-11-08 |
| Merging into `main` | Yes, for independently reviewed `[READY]` PRs when checks pass; agents set the prefix and merge under the [PR lifecycle](../policies/08-merge-readiness.md#pr-lifecycle) | User, 2026-11-08; kept 2026-11-09 |
| Workstreams | Enabled, in `.vibe-workflow/settings.json` | User, 2026-11-09 |
| `.vibe-workflow/project.json` in Git | Not committed; `.gitignore` lists `/.vibe-workflow/project.json`, `/.vibe-workflow/.d4-*` and `/.vibe-workflow/private/`, and `.vibe-workflow/settings.json` is committed | User, 2026-11-09 |

The repository has no CI or branch protection, so the merge permission relies on the local checks and independent review.

## Document mapping

| Responsibility | Location |
| --- | --- |
| Product and overview | `README.md`, `docs/onboard-my-project-plan.md` |
| Shared instructions | `AGENTS.md` |
| Client instructions | `CLAUDE.md` imports `AGENTS.md` (Codex reads `AGENTS.md` directly) |
| Architecture | `clients/index.md`, `policies/00-index.md`, `docs/onboard-my-project-plan.md` |
| Design and accessibility | Not applicable: no user interface |
| Local preparation and quality | `README.md` "Development" (setup and install); [development guide](development.md) (where to work, checks, PRs) |
| Setup and evidence | This file |

## Component assessment

Reused user-level installs, no project change: Superpowers, Ponytail, RTK, Context7, Claude memory (Caveman verified in Claude Code only). Not applicable: ESLint/Biome (no JS/TS), browser tools and Chrome DevTools MCP (no UI), Vercel skills, Next.js, PostgreSQL, shadcn, Sentry, Anthropic artifact skills, Obsidian tooling (existing `~/ai-memory-vault` is separate; `AGENTS.md` points to it), project Git hooks (excluded by the Toolkit), `gh-fix-ci` (no GitHub Actions; revisit if CI is added). Security and code review use the local policies in `policies/04-security-review.md` and `policies/06-code-review.md`. Graphify: no graph exists; generating one is a separate choice and is pending.

Harness improvement: native method adaptation, the default because no other mode was chosen. The current agent compares a baseline and one candidate using this repository's own checks. It is inspired by Harness2, but it does not run the Harness2 framework or reproduce its algorithm or benchmark results. The official Harness2 CLI lab is not selected and is not installed.

- **How to use it:** when a concrete instruction, memory, context-routing or tool-use failure recurs, follow "One bounded improvement" in `policies/09-workflow-maintenance.md`, with protected inputs from `clients/harness-evaluation.md` and `assets/harness-evaluation/`. The limit is one candidate comparison, then report. More rounds need a concrete reason and an agreed limit. Source: Toolkit prompt 15 and `docs/tools/23-harness2.md` at `790a895`. That was still the latest commit for prompt 15 when checked on 2026-11-09. Routine use needs no Toolkit fetch.
- **Initial smoke run (2026-11-09):** run in an OpenCode session hosting the Claude Code harness, model Opus 5.5 (authentication mode not observed), on macOS arm64 with Node 26.13.0 and Python 3.15.2. Inputs were copied to a disposable directory under `/tmp`, outside this checkout. The checks took under a second.
  - Pass: the boundary baseline, `node scripts/validate-harness.mjs <copy>`, gave 7 results. Only "denies matching user IDs across tenants" and "denies missing, empty, or non-string identities" failed. The fixture bytes did not change.
  - Pass: `python3 -B check.py` succeeded on the `phase-two/minimal` input, on that input with `expected-final` laid over it, on the `phase-two/customized` input, and on that input with `expected-after-partial-undo` laid over it. The last copy kept the Friday-release note and `src/AGENTS.md`.
  - Pass: before/after SHA-256 snapshots showed no change to `assets/harness-evaluation/` or the evaluator.
  - Not run: the stale-handoff and read-only-review cases, which need a fresh native worker. None was dispatched, and a worker in this OpenCode-hosted session would not count as a Codex or Claude Code run. Lifecycle and title observations are historical only (see `assets/harness-evaluation/cases.md`).
  - These deterministic checks prove the inputs and evaluator are portable. They do not prove instruction loading, client behavior or general reliability.
- **Decision:** no failure was observed, so no trial ran and no change was adopted or rejected. Reapplying setup updates this entry in place. Reuse these results while the fixtures, evaluator, Node and Python stay the same, and run the smoke checks again after material changes. Next: run one native stale-handoff case in Codex or Claude Code when a client or instruction change calls for requalification.

## Checks run

- `python3 -B scripts/validate.py`: passed (package only; client execution not checked).
- `python3 -B -m unittest discover -s tests`: baseline run started before setup edits; it had not finished when this record was written, so no result is recorded yet
- Source-size screening (brief, partial): 62 of 363 tracked Python files exceed the 350-line reference; largest are `runtime/evidence.py`, `runtime/changes.py`, `runtime/delegation.py`. Structural impact is unknown and not a blocker. Use `review-my-project` for a detailed assessment.

## Client integration

- Registration: `vibe-workflow@vibe-workflow-local` in Codex and Claude Code (user scope). Since 9 November 2026 the marketplace source is the clean export `~/.cache/vibe-workflow/dev-package`, built by `scripts/package.py`, instead of this checkout: copying the checkout put ignored files and Git history into the caches. Refresh: rerun `python3 -B scripts/package.py`, then reinstall in Codex and approve the changed hooks; Claude Code runs the export folder directly. For installed versions, sources and the expected-version record, run `python3 -B scripts/workflow.py upgrade status`.
- Cache cleanup (9 November 2026): removed Claude's orphaned 0.1.0 copy with `--prune-caches`, and ten `vibe-coding-workflow@vcw-*` qualification installs with `codex plugin remove`. Their Codex hook-trust entries remain in `~/.codex/config.toml`. Codex's `.marketplace-plugin-source-staging` holds other marketplaces' leftovers and was left alone.
- Startup membership: canonical startup-only `.vibe-workflow/project.json` for `codex` and `claude-code`, status `configured`, no runtime. The record is local to this checkout and ignored by Git; other clones and worktrees need their own onboarding.
- Codex installation from the clean export and its startup hook are qualified in a disposable home on Codex 0.167.0 (CLI app-server, unauthenticated, SessionStart only); see [dev package validation](dev-package-validation.md).
- Startup observations on this machine are dated evidence in [dev package validation](dev-package-validation.md#real-sessions-on-this-machine).
- Package scan and its two bounds: the setup observation's Codex bundle scan reads an installed export as it is, and a checkout as the export `scripts/package.py` would build from it: tracked files minus the development-only paths in `runtime/package_manifest.py`, so untracked, ignored and `dist/` files never count. That scan keeps Codex's own `codex-plugin-bundle-v1` limits (`PACKAGE_SCAN_MAX_BYTES` 8 MiB in total, 1 MiB per file, 4096 paths), which Codex owns: a package the scan accepted but Codex rejected would fail at install. Onboarding's project inventory bound, `MAX_TOTAL` in `runtime/onboarding.py`, is separate: 16 MiB since 9 November 2026 (the user's decision), so this repository (about 9.3 MiB with `tests/`) fits. Only the inventory's own budgets carry it; each startup observation it makes keeps its own 8 MiB entry bound. Every other aggregate that used to share `MAX_TOTAL` keeps 8 MiB as `LEGACY_MAX_TOTAL`: repository screening (its budget is shared with review storage), review storage, the birth artifact inventory, retained Toolkit sources and any other owner's budget passed to onboarding's observers. `python3 -B scripts/validate.py` reports the export as `package_export`, fails when it exceeds a bundle limit or still holds a development-only path, and warns at 75% of the bytes. Membership uses Git's stored names, so a file whose name differs on disk only by Unicode normalization would be left out of a checkout's scan; none does today, and the checkout-versus-export test would catch it.

## Rename migration (8 November 2026)

0.1.33 renamed the plugin and its state paths; nothing migrates automatically. On this machine:

- Copied and verified identical, then removed the old copies: the Workstreams family register (`~/.vibe-workflow/workstreams-families.sqlite3`) and the usage directory (`~/.local/state/vibe-workflow/usage`, including its one open run). The new code loads the ledger and upgrades it on first write. Routing is off for both enrolled checkouts, because per-checkout activation is keyed to the enrollment path and content that changed. Status reports `reusable_setup_unavailable`, so the previous setup cannot be reused as is; set routing up again.
- Not copied: `machine.json`, which records the old directory chain, and the old review storage under `~/.local/share/vibe-coding-workflow`.
- Kept for now, pending a decision: old onboarding, update and proposal records under `~/.local/state/vibe-coding-workflow/`, including rollback journals for earlier enrollments.
- Not changed: `/Users/fcouto/dev/git/opus/AGENTS.md` still has a `vibe-coding-workflow:` Workstreams marker and a link into an old plugin cache, so its Workstreams choice reads as not configured.

## Maintenance and undo

- Private plan, backups and journal: `~/.local/state/vibe-workflow/onboarding/vibe-workflow-20261108/`.
- Undo: run `rollback` for that plan directory. It restores only content still matching this operation's output and keeps later edits.
- Reapply: use `onboard-my-project` again; it preserves existing sections and registrations.
- Transaction lock directory (`~/.local/share/vcw-transactions`, `runtime/changes.py`): production never deletes its lock files or owner markers, so test runs and abandoned checkouts leave many of them. `python3 -B scripts/prune_state.py` reports what is provably stale (dry run by default). Stop every agent session and test run first. `--yes` deletes empty old locks; `--yes --markers` also moves markers whose plan directory is gone into a private run directory with a manifest (undo steps are in `--help`); `--max-delete` caps a run. It never deletes markers, only moves them.
