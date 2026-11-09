# Setup record

Project: `vibe-workflow` (this repository is the plugin itself; enrolled by explicit user selection on 2026-11-08). Clients: Codex and Claude Code. Shape: standalone repository, remote `fcoouto/vibe-workflow`, primary branch `main`.

## Source

Vibe Coding Toolkit `soumatheusgomes/vibe-coding-toolkit` at `21b71b68679c11184f49c23168852014bf6dde8b` (online, 2026-11-08). Setup source only: nothing here fetches or runs Toolkit content.

## Decisions

| Choice | Answer | Provenance |
| --- | --- | --- |
| Publishing | Commit, push a task branch, open a draft PR | User, 2026-11-08 |
| Merging into `main` | Yes, for independently reviewed `[READY]` PRs when checks pass | User, 2026-11-08 |
| Workstreams | Not configured: the `AGENTS.md` block was removed in `502896f`; set it again with the Workstreams skill | Block removed 2026-11-09; choice pending |
| `.vibe-workflow/project.json` in Git | Not committed; `/.vibe-workflow/` is in `.gitignore` | User, 2026-11-09 |

The repository has no CI or branch protection, so the merge permission relies on the local checks and independent review.

## Document mapping

| Responsibility | Location |
| --- | --- |
| Product and overview | `README.md`, `docs/onboard-my-project-plan.md` |
| Shared instructions | `AGENTS.md` |
| Client instructions | `CLAUDE.md` imports `AGENTS.md` (Codex reads `AGENTS.md` directly) |
| Architecture | `clients/index.md`, `policies/00-index.md`, `docs/onboard-my-project-plan.md` |
| Design and accessibility | Not applicable: no user interface |
| Local preparation and quality | `README.md` "Development" |
| Setup and evidence | This file |

## Component assessment

Reused user-level installs, no project change: Superpowers, Ponytail, RTK, Context7, Claude memory (Caveman verified in Claude Code only). Not applicable: ESLint/Biome (no JS/TS), browser tools and Chrome DevTools MCP (no UI), Vercel skills, Next.js, PostgreSQL, shadcn, Sentry, Anthropic artifact skills, Obsidian tooling (existing `~/ai-memory-vault` is separate; `AGENTS.md` points to it), project Git hooks (excluded by the Toolkit), `gh-fix-ci` (no GitHub Actions; revisit if CI is added). Security and code review use the local policies in `policies/04-security-review.md` and `policies/06-code-review.md`. Graphify: no graph exists; generating one is a separate choice and is pending.

Harness improvement: native method adaptation. No observed instruction, memory, context-routing or tool-use failure justifies a trial, so no working instruction was changed. To reapply, follow the Toolkit's harness-improvement procedure when a concrete failure appears, using a disposable copy and unchanged acceptance checks.

## Checks run

- `python3 -B scripts/validate.py`: passed (package only; client execution not checked).
- `python3 -B -m unittest discover -s tests`: baseline run started before setup edits; it had not finished when this record was written, so no result is recorded yet
- Source-size screening (brief, partial): 62 of 363 tracked Python files exceed the 350-line reference; largest are `runtime/evidence.py`, `runtime/changes.py`, `runtime/delegation.py`. Structural impact is unknown and not a blocker. Use `review-my-project` for a detailed assessment.

## Client integration

- Registration: `vibe-workflow@vibe-workflow-local` in Codex and Claude Code (user scope). Since 9 November 2026 the marketplace source is the clean export `~/.cache/vibe-workflow/dev-package`, built by `scripts/package.py`, instead of this checkout: copying the checkout put ignored files and Git history into the caches. Refresh: rerun `python3 -B scripts/package.py`, then reinstall in Codex and approve the changed hooks; Claude Code runs the export folder directly. For installed versions, sources and the expected-version record, run `python3 -B scripts/workflow.py upgrade status`.
- Cache cleanup (9 November 2026): removed Claude's orphaned 0.1.0 copy with `--prune-caches`, and ten `vibe-coding-workflow@vcw-*` qualification installs with `codex plugin remove`. Their Codex hook-trust entries remain in `~/.codex/config.toml`. Codex's `.marketplace-plugin-source-staging` holds other marketplaces' leftovers and was left alone.
- Startup membership: canonical startup-only `.vibe-workflow/project.json` for `codex` and `claude-code`, status `configured`, no runtime.
- Codex installation from the clean export and its startup hook are qualified in a disposable home on Codex 0.167.0 (CLI app-server, unauthenticated, SessionStart only); see [dev package validation](dev-package-validation.md).
- Startup observations on this machine are dated evidence in [dev package validation](dev-package-validation.md#real-sessions-on-this-machine).

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
