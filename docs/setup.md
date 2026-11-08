# Setup record

Project: `vibe-workflow` (this repository is the plugin itself; enrolled by explicit user selection on 2026-11-08). Clients: Codex and Claude Code. Shape: standalone repository, remote `fcoouto/vibe-workflow`, primary branch `main`.

## Source

Vibe Coding Toolkit `soumatheusgomes/vibe-coding-toolkit` at `21b71b68679c11184f49c23168852014bf6dde8b` (online, 2026-11-08). Setup source only: nothing here fetches or runs Toolkit content.

## Decisions

| Choice | Answer | Provenance |
| --- | --- | --- |
| Publishing | Commit, push a task branch, open a draft PR | User, 2026-11-08 |
| Merging into `main` | Yes, for independently reviewed `[READY]` PRs when checks pass | User, 2026-11-08 |
| Workstreams | Enabled | Existing block in `AGENTS.md` |
| `.vibe-workflow/project.json` in Git | Not committed (default) | Unanswered; kept local |

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
- Full suite (`python3 -B -m unittest discover -s tests`): not run. The setup changes are documentation only (`README.md`, `AGENTS.md`, `CLAUDE.md`, this file), and `AGENTS.md` asks for the full suite only when runtime, hook or policy behavior changes. Instead, the 19 test modules that read those files or the onboarding/runtime code ran in parallel, one process per module, and all exited 0, together with `scripts/validate.py` (2026-11-08). No pre-existing failure is known; the full-suite baseline is unrecorded.
- Source-size screening (brief, partial): 62 of 363 tracked Python files exceed the 350-line reference; largest are `runtime/evidence.py`, `runtime/changes.py`, `runtime/delegation.py`. Structural impact is unknown and not a blocker. Use `review-my-project` for a detailed assessment.

## Client integration

- Registration: both plugins installed and enabled as `vibe-workflow@vibe-workflow-local` 0.1.32 (Codex and Claude Code, user scope).
- Startup membership: canonical startup-only `.vibe-workflow/project.json` for `codex` and `claude-code`, status `configured`, no runtime.
- Claude Code, observed: a disposable `claude --init-only` run executed the installed plugin's `SessionStart` hook once and returned the enrollment context.
- Codex, observed: hook trust was confirmed by the user; a disposable `codex exec` session ran the `SessionStart` hooks without a trust warning, and the session record contains the injected "Vibe Coding Workflow applies to this enrolled checkout" context.
- Not observed: the model acting on that context. The Codex run stopped at a usage-limit error before any model reply. Codex Desktop and a conversational Claude Code session were not observed either. These remain pending.

## Maintenance and undo

- Private plan, backups and journal: `~/.local/state/vibe-workflow/onboarding/vibe-workflow-20261108/`.
- Undo: run `rollback` for that plan directory. It restores only content still matching this operation's output and keeps later edits.
- Reapply: use `onboard-my-project` again; it preserves existing sections and registrations.
