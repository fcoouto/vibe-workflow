# vibe-workflow

## Start here

**Your first tool call in every session is to read the memory index described in [Memory](#memory-and-project-preferences).** Do it before any other read, search or command, for a question as much as for a task.

Delegation is not a factual-only question, even for a read-only check or a worker requested by name. Before considering or calling a worker, read the [policy index](policies/00-index.md) in full and follow its loading rules.

A factual question about the repository needs only the memory read: answer it from the actual files, then stop. Before other repository work—including file, setting, Git or PR changes, reviews, check selection, completion or readiness decisions and resuming work—read the [policy index](policies/00-index.md) in full. Follow its single routing table and read every applicable contract and guide in full before the action it guards.

Hooks supplement these instructions. Their presence or silence does not establish policy compliance, enforcement or readiness. Ordinary development remains independent of optional managed controls.

## Product and stack

- Product and scope: [README](README.md). A Codex and Claude Code plugin that wraps the Vibe Coding Toolkit as local policies, skills, hooks and scripts.
- Stack: Python 3.11+ standard library and Git, on macOS or Linux. There is no package manager, linter, type checker, build step or CI.
- Setup decisions, maintenance and undo: [setup record](docs/setup.md).

## Canonical commands

Run them in the task worktree. Install, lint, types and build are unavailable: nothing exists to run.

- Validate the package: `python3 -B scripts/validate.py`
- Commit first, then run tests selected by what the change can break: `python3 -B scripts/test_sharded.py <module-or-glob>...` (without names it runs the full suite, about 10 minutes)
- Dev package for both clients: `python3 -B scripts/package.py`

## Git and collaboration

The [development guide](docs/development.md) owns these rules and their detail.

- The primary checkout on `main` is for reading and coordinating. Never change a tracked file or commit there, however small the change. A command that rewrites a tracked file, such as a feature setting, counts.
- Make every change in a task worktree on its own branch from current `main`. Create the worktree with the client's native worktree tool when it has one. Your next command there, before any edit or settings command, is `git switch -c <type>/<topic>`, unless the tool already put the worktree on a named branch.
- Publish a completed change without asking again: validate, commit, run the tests the change needs on the committed change, push the task branch and open a draft PR against `main` titled `[BLOCKED] <title>`, its body from the [PR template](templates/pull-request.md). The [setup record](docs/setup.md#decisions) holds this publishing choice; a request that says only "edit" or "commit" does not narrow it. Stop earlier only when the user says to.
- The draft PR publishes the change; it does not finish it. The [PR lifecycle](policies/08-merge-readiness.md#pr-lifecycle) owns what follows (review, `[READY]`, merge), within the merge permission in the setup record.
- Report what you ran, its result and what you did not run. Report package validation and observed client execution separately.

## Working agreements

- Read relevant code and instructions before editing. State assumptions that affect behavior or scope.
- Complete the request with the smallest sufficient change. Prefer existing code and native capabilities; add no abstraction, option, dependency or feature for an unrequested need.
- Preserve local changes, explicit requirements, and the user's permissions, model choices, subscriptions and customizations.
- Do not turn assumptions into facts or pending work into results.
- When a request conflicts with a rule in this file, do not carry out the conflicting part. Name the rule, do what still complies and offer the compliant alternative.
- Continue authorized work. Ask only for essential information or for actions outside the authorization.
- Messaging third parties, costs, destructive actions and publishing beyond the recorded choice need explicit authorization. Installing a tool does not grant it.

## Execution continuity

- Answer a status question, then resume the remaining authorized actions. Stop when the user pauses, cancels or changes the objective.
- A blocker stops only the work that depends on it. Before stopping, check for in-scope actions you can still take.
- Resuming from a handoff: check actual files, Git state, checks and processes before relying on it. Claim background execution only when you can verify an active process.
- Do not repeat an identical attempt without new evidence. Respect cost, time and retry limits, gates and denials.
- If a worker fails or its state is uncertain, confirm it has stopped before taking over its files.
- Keep a short handoff for unfinished multi-session work in the existing plan or task record ([Context and Handoff](policies/07-context-and-handoff.md)), not in permanent memory.
- With no safe alternative, report the blocker, the completed work and what is needed to resume.

## Tools and style

- Superpowers, Ponytail, RTK, Context7 and Caveman are the user's own installs. Use the relevant ones and scale them to the task; explicit instructions and already authorized execution take precedence over repeated approvals.
- Verify that a tool is installed and discovered before relying on it. A written preference installs nothing; record an absence or use an officially supported alternative.

## Memory and project preferences

- Feature settings: this project's choices are in `.vibe-workflow/settings.json`. Before work that depends on a feature, run `python3 -I -B scripts/workflow.py features status --cwd .`; change them only through its commands (`enable`, `disable`, `set`, `unset`, `undo`, `migrate`), in the task worktree because the file is tracked. Feature authors follow [feature settings](clients/features.md).
- Memory: the user-level vault `~/ai-memory-vault` is plain files, with no vault software or MCP server. Before project work, read its `MEMORY.md` index and open topic files only when relevant. Before saving, updating, migrating or pruning, read its `INSTRUCTIONS.md` in full and follow its narrow test and growth policy. Keep this section intact; it is the vault's load path.
