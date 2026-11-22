# Title meter: record the branch at push and merge, and keep diagnostics

## Status and intent

A real session's meter froze at 2 cells and showed `WAITING` after its PR was created and
merged. The user approved this plan on 2026-11-22 after two rounds between two models and a
review by a third. This is PR A of two. PR B (multi-instance ownership of a stored record)
waits for a throwaway check of how the host delivers events to plugin instances.

Evidence (verified 2026-11-22):

- Stored records (`kv` table, keys `session-titles/<id>`): all three records from this
  project have `remembered: null`; the one from another project has it set.
- A replay of the real plugin with real git reaches cell 5 in the normal flow and sticks at
  cell 4 when push, merge, detach and worktree removal all happen before any turn end. After the
  worktree is gone the PR is found only through `remembered`.
- `declaredPaths` ignores paths written on the `**Files:**` line itself; 5 of 16 plans here
  use that form, which caps those tasks at cell 2.
- Every read failure in `readFacts` is swallowed, so a frozen meter explains nothing.

## Goals

- A push, `gh pr create` or `gh pr merge` in a bound session records the task's branch, so
  the meter can reach cell 5 when merge and cleanup finish inside one turn.
- Same-line `**Files:**` plans count their paths.
- A saved record says what the last read saw and which step failed, so the next frozen
  meter is explained by data.

## Non-goals

- Ownership of a record across plugin instances or locations (PR B).
- Any note, skill-text or system-prompt injection: the partner skills decide when agents
  branch, push and finish, and nothing here needs an agent habit.
- Looking up the PR by commit, binding on shell redirects, `session_rename` handling.

## Anti-goals

- Crediting the wrong task: the branch is read from the bound checkout (`task.top`), never from
  the command's directory, and the default branch is never recorded.
- Storing secrets: diagnostics hold a branch name, booleans, a PR state, a step name and an
  error code, never stderr, paths or file contents.
- Writing the title or running a write command at tool time.
- A storage write on every turn: diagnostics hold no timestamps and join the save snapshot, so
  an unchanged read writes nothing.

## Approaches

- **A. Record the branch at tool time, plus diagnostics (selected).** Plugin-only observation;
  closes the one-turn dead end and makes the unexplained part visible.
- **B. Look up the PR by the task's commit SHA.** Rejected: misses rebases, can match another
  PR, and costs a GitHub search per turn.
- **C. Tell agents to push early or rename carefully.** Rejected: partner skills own those steps.

## Design

1. `declaredPaths`: on a `**Files:**` line, also read backticked path-like tokens after the label.
2. `readFacts` returns `errors: [{ step, code }]` for its swallowed reads (`status`, `base`,
   `diff`, `plan`, `pr`); `code` is a short string (`ENOENT`, `ETIMEDOUT`, `exit:<n>`, `error`).
3. `session-facts.js` exports `branchOf({ top, run })`: the checked-out branch of `top`, or null
   when detached, on the default branch, or unreadable.
4. In `session-titles.js`, the existing `execute.after` handler also accepts a completed shell
   call (`shell` or `bash`) whose command `classify` (from `pr-advice.js`) reports as `push`,
   `pr_create` or `pr_merge`. For a root session with a live binding it queues one job: if the feature
   is on, `branchOf` the bound checkout and store the branch in `task.remembered` (saved). Exit
   codes are ignored (the hook fields are unverified on the host); no upstream is required.
5. The saved record gains `diag: { instance, exists, branch, upstream, head, pr, errors }`,
   refreshed at every evaluation, validated on load, and part of the save snapshot. `instance` is
   a short random id per plugin instance.

## Known gaps (accepted)

A compound command that detaches or removes the worktree before the hook runs; a merge from the
web UI or another tool with no earlier push seen; a record read by a different instance (PR B).

## Constraints

Node ES modules, standard library only; advisory only (reads git, never writes); tests first;
no README or onboarding change; no injected notes, so the live injection check is not triggered.

## Calls made (the writer's)

- `classify` is reused from `pr-advice.js` instead of a second command parser.
- The title is not rewritten at tool time; the next turn end does it.
- Diagnostics live at the record's top level, outside the `task` whitelist.

## Proof expected

- A one-turn flow (push, merge, worktree removed, one turn end) reaches 5 cells; without the
  trigger it stays at 4.
- Same-line, next-line and mixed `**Files:**` forms parse; prose tokens without `.` or `/` stay out.
- A failing git read shows its step and code in the record; no stderr or path is stored.
- An unchanged turn still writes nothing to storage.
