# Session titles

## Status and intent

The user asked to bring back the idea of the earlier plugin's "Title Sync",
redesigned and as thin as possible: **the status of a task lives in its session's
title**, kept current, best effort. This document does not authorize implementing or
installing anything; the implementation plan follows its approval.

Why a redesign, not a port: the earlier plugin competed with partners. It kept its own
task database, milestone weights, attention labels, a manual-edit pause, locks and a
proof-token rename script (about 840 lines), because it had to *infer* status through a
protocol the model followed. This plugin observes; partners (superpowers) do the work.

The only hard requirement: thin and simple.

## Evidence (2026-11-20)

Tool-call timelines of today's sessions in this repository, read through `opencode api`,
covering three complete tasks (PRs #335, #336-338, #346) and this planning session:

- Each task: worktree and branch, spec `write`, builder-check subagent, approval, plan
  `write`, plan-review subagent, approval, implementation, a "final branch review"
  subagent, one push and PR at the end, merge.
- Spec and plan files are always *created* with the `write` tool; later fixes are mostly
  shell heredocs.
- The executor skill (`executing-plans`, `subagent-driven-development`) loaded in 1 of 3
  tasks; most product edits are shell heredocs. Tool traces are unreliable after
  planning; git state is reliable.
- Plan checkboxes are never ticked (0 of 4 finished plans). Commits are not 1:1 with plan
  tasks. Every plan has `**Files:**` blocks; the three newer ones use `- Create:` /
  `- Modify:` / `- Test:` lines, the older ones another layout.
- Unrelated PRs merge in the middle of a task (#334 during the design of #335), and small
  fix PRs without a design are interleaved (#339-345). Closure must be bound to the
  task's own branch.
- OpenCode-created worktrees start on a detached HEAD (this session's does); the agent
  creates the branch later.

Verified on the installed host (OpenCode 2.3.2) with a throwaway plugin in a throwaway
project, removed afterwards:

- `ctx.event.subscribe` and `ctx.tool.hook` exist for a plugin.
- `session.execution.started` and `session.execution.succeeded` reach the plugin with
  `data.sessionID`, for root and child sessions, once per turn. **`session.idle` was
  never delivered** (subscribed, zero events), although the API schema lists it.
- A root turn can end while its background child still runs; the child's report then
  wakes the root for another turn.
- `execute.after` for `write` carries `sessionID` and `input.path`, **relative to the
  session's directory** (`docs/superpowers/specs/...`).
- `ctx.session.get` returns `id, projectID, cost, tokens, time, title, location`, plus
  `parentID` for a child.
- A title set with `ctx.session.update` (with the leading `U+FFFB`) stayed through two
  later turns.
- `gh` with the server's PATH and HOME authenticates through the keyring;
  `gh pr list --head <branch> --state all` finds a PR merged with `--delete-branch`
  (about 0.5 s) and returns `[]` for an unknown branch.
- `git diff --name-only --merge-base origin/main` works on a detached worktree (committed
  and uncommitted changes); `git status --porcelain=v2 --branch -uall` adds untracked
  files and shows `(detached)` until a branch exists.

## Goals

- A root session that starts a task shows its stage in the title:
  `<U+FFFB><5 cells> <topic>`, or `<U+FFFB><5 cells> WAITING · <topic>` when the session
  has stopped and the task is not done. `U+FFFB` is invisible and keeps the meter at a
  stable width.
- Five cells, as the user allocated them:

  | Cells | Stage | Fact |
  |---|---|---|
  | 1 | Planning | a spec was written in this session |
  | 2 | Executing | any changed path outside `docs/` on the task's checkout |
  | 3 | Executing | at least half of the plan's declared paths changed |
  | 4 | Executing | all declared paths changed, or a PR exists for the branch |
  | 5 | Review + closure | the branch's PR is merged, or closed as `[SUPERSEDED]`, and cleanup is satisfied |

  The meter shows the highest cell whose fact holds; facts are not prerequisites of
  each other, so a merged PR closes the task even if one declared file was never
  touched or the task changed only `docs/` *(author's call: the user defined the end
  of a task as delivery plus cleanup)*.

- A task starts when a spec is written; a new spec in the same session becomes the task,
  done or not. A task ends at cell 5.
- No model call, no polling, no database, no new dependency.

## Non-goals and anti-goals

- No `BLOCKED` (first council: it collided with the `[BLOCKED]` PR prefix and has no
  observable trace). No review detector: a merged PR implies review under the PR
  workflow, and reviewer naming is model-chosen (second council, user approved).
- No checkbox parsing, commit counting, executor-skill or reviewer-name detection.
- No title for work that never writes a spec (quick fixes stay untitled).
- Anti-goals: overwriting what the user typed (only our prefix is swapped); renaming a
  child session; crediting the wrong work (on any doubt, hold the last cells).
- The title is a display, not a source of truth.

## Behavior

### Binding

- **Bind** on a completed `write` in a root session whose path matches
  `(?:^|/)docs/superpowers/specs/[^/]+\.md$`. Resolve a relative path against the
  session's directory (`ctx.session.get(...).location.directory`). The task checkout
  `top` is the path before `docs/superpowers/`.
- A `write` of a **different** spec path replaces the binding and resets to cell 1. A
  rewrite of the same spec changes nothing.
- A completed `write` of `docs/superpowers/plans/*.md` under the same `top` records the
  plan path. *(Author's call: a plan written with no binding also binds, so a plan-first
  task is not lost.)*
- The binding is in memory: `sessionID -> { top, spec, plan, branch, cells }`, capped at
  128 sessions like the existing `notices` map. A server restart loses it and the title
  stays as it was until the next spec write (accepted by the user).
- Child sessions and other checkouts are never read, so unrelated PRs and fix branches
  cannot move a meter.

### Evaluation

- At the end of every turn of a bound root session (`session.execution.succeeded`,
  `.failed` or `.interrupted`), evaluate the facts and rewrite the title.
- Facts, all read-only, each call bounded (git 2 s, gh 5 s), errors leave the cells as
  they were:
  1. `git -C <top> status --porcelain=v2 --branch -uall`: branch name, upstream,
     untracked files. Remember the last branch seen with an upstream (never the
     default branch), because cleanup detaches or removes the checkout; with no such
     branch, no `gh` call is made.
  2. `git -C <top> diff --name-only --merge-base origin/main`: committed and uncommitted
     changes since the merge-base.
  3. `gh pr list --head <branch> --state all --json state,title --limit 1`, run only
     once the branch has an upstream or the checkout is gone, from a directory of the
     same repository.
  4. Whether `top` still exists (one `stat`).
- Declared paths: backticked tokens on the lines of each `**Files:**` block of the plan,
  read once per evaluation, `:line` suffixes stripped, deduplicated. A plan with no
  usable paths skips cell 3.
- Cells never go down within a task: `max(previous, computed)`.
- Cleanup is satisfied when `git_workflow.worktree_cleanup` is not `true`, or `top` is
  the primary checkout, or `top` no longer exists, or the PR was closed as
  `[SUPERSEDED]` (the PR procedure keeps that branch on purpose) *(author's call)*.
- At cell 5 the binding is released and nothing is evaluated for that session until the
  next spec write.

### When the title is written

- Only at turn boundaries: the meter and label at the end of a turn, the label's removal
  at the start of the next. A spec write records the binding but does not rename; the
  first rename happens at the end of that turn *(author's call)*. Reason: OpenCode
  generates its own title with a model during the first turn; writing at the turn end
  keeps us from racing it and from mistaking its rename for the user removing our
  prefix.

### The `WAITING` label

- Added at the end of a turn when the session is bound and not done; removed at
  `session.execution.started`. When you see `WAITING`, the session has stopped and needs
  you to move it.
- A root turn can end while a background child still runs; the label then shows until
  the child's report wakes the session (seconds to minutes). Accepted.

### Topic and guards

- Keep whatever follows our prefix, so a topic the user edited survives. On the first
  binding that is usually OpenCode's own generated title. An empty or OpenCode-default
  title (`New session - <time>`) takes the topic from the spec file name (date and
  trailing `-design` removed, dashes to spaces).
- On a new task in the same session, the topic switches to the new spec's name only if
  the title is still exactly what the plugin last wrote; if anyone changed it since,
  keep it. The binding stores that last written title *(author's call)*.
- Root sessions only. Opt-in only. If the plugin renamed a session and the title has
  lost its prefix, the user removed it: never rename that session again until restart.
- An unchanged title writes nothing. Failures are silent and never touch tool results or
  start a model turn. The read-then-write rename race is accepted by the user.

## Approaches

1. **Facts (chosen).** One trace binds (the spec write); everything after is read from
   git and GitHub at the end of a turn. Idempotent: a missed event is repaired at the
   next turn end. Catches web merges. About 150-200 runtime lines.
2. **Traces.** Infer stages from skill loads, commits and `gh` commands. Rejected by the
   evidence: the executor skill loaded in 1 of 3 tasks, edits go through heredocs, web
   merges leave no trace, unrelated PRs do.
3. **Hybrid** (Astra). Traces trigger reads, facts decide; pin the PR from
   `gh pr create`. Rejected for simplicity: the head-branch lookup already binds the PR,
   and pinning adds parsing without catching more.

## Settings

- A `session_titles` feature, `{"session_titles": {"enabled": true}}`, written by
  onboarding at the main project path, like `git_workflow`. Not onboarded or off means
  no hooks do any work.
- Onboarding asks one more question, always (not tied to the git workflow answers),
  after the existing ones; the procedure's "up to four questions" becomes five. It
  recommends "on" (the setting itself stays off until answered). Its copy follows the
  existing question rules and goes through the `impeccable` skill. Readiness reports
  `setup_needed` when the host lacks `ctx.event.subscribe` or `ctx.tool.hook`
  *(author's call)*.
- Reads `git_workflow.worktree_cleanup` for the cleanup rule.

## Constraints

- Advisory posture holds: the plugin only reads git/gh and writes a session title.
- Node ES modules and the Python standard library only; no install or build step.
- A new exported file goes in `scripts/manifest.py`, `package.json` `files` and
  `tests/test_package.py`. README edits only through the `impeccable` skill.

## Known gaps (documented, not closed)

- The `opencode` CLI on this machine's PATH is 2.2.6; the server that loads plugins is
  2.3.2, where the facts above were verified. Older servers are unverified.
- If OpenCode's title generation finishes after the first turn ends, it can replace our
  first title and the session is then treated as user-renamed. Check during
  implementation, against a real first-turn spec write.

- A web merge after the session's last turn stays invisible until the session runs
  again; the title stays `▰▰▰▰▱ WAITING`.
- A restart loses bindings; titles freeze until the next spec write.
- File coverage measures breadth, not correctness; scaffolding can fill cells early, and
  declared files never touched can hold cell 3.
- A stale `origin/main`, or `main` merged into the branch, shifts the diff.
- A spec written by a shell command (not `write`) does not bind.
- Squash merges are detected through `gh`, not git ancestry; without `gh` access cell 5
  is never reached.

## Left to the builder

- The parser details of `**Files:**` blocks and the exact default-branch detection.
- Test fixtures (fake `ctx`, fake event stream, fake `git`/`gh` runner).
- How to share the existing `execute.after` registration with `pr-advice.js` without
  breaking its tests.
