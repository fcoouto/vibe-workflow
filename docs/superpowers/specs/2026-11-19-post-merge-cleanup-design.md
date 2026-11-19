# Post-merge cleanup

## Status and intent

The user asked to bring the earlier plugin's post-merge cleanup into this repository,
as an opt-in onboarding choice under the `git_workflow` feature. This document does
not authorize implementing, installing, or changing any GitHub setting; the
onboarding question changes the GitHub setting only after the user says yes.

Why: finished task worktrees and branches pile up. In this repository there are 2
registered worktrees, 16 local branches (8 already merged) and 156 branches on
`origin`, and GitHub's "delete head branch on merge" is off. No partner tool cleans
up after a merged pull request.

Guiding rule from the user: do not spend effort on something a third party delivers
reliably. So the remote branch is GitHub's job, and our feature covers only what
nothing else does: the local worktree and its local branch.

Reference: the earlier plugin's `clients/delivery.md` "Post-merge cleanup" (opt-in,
off by default, remote deletion a separate switch, exact merged-head checks, a
helper script). Its runtime and helper are not brought over.

## What partners already do (researched 2026-11-19)

| Partner | Remote branch | Local branch | Worktree |
|---|---|---|---|
| Superpowers `finishing-a-development-branch` | never | `git branch -d` after a local merge only | removes only worktrees under `.worktrees/` or `worktrees/`, after a local merge; keeps it after a PR; never forces |
| Superpowers `using-git-worktrees` | none | none | defers cleanup to the harness |
| OpenCode | none | none | `worktree_remove` (recorded worktrees; `edit` permission on the directory; `force` discards work); nothing automatic |
| Claude Code (docs read) | none | removes the worktree's own branch with a clean worktree on exit | removes clean worktrees of unnamed sessions on exit, prompts otherwise; has `ExitWorktree`; never sweeps a worktree made with `git worktree add` |
| Codex (search snippets only, not read in full) | none | none | deletes older Codex-managed worktrees past a configured limit, after a snapshot |
| GitHub / `gh` | repo setting "delete head branch on merge", or `gh pr merge --delete-branch` | the `gh` flag also tries the local branch | none |
| git | `git push --delete` | `git branch -d` refuses unmerged work and a branch checked out in a worktree | `git worktree remove` refuses modified or untracked files |
| caveman, ponytail, impeccable, rtk, context7, graphify | none | none | none |

Nothing deletes a worktree or branch because its pull request merged.

## Goals

- An onboarding choice that, once on, has agents clean up after a confirmed merge:
  remove the task's worktree and its local branch.
- Onboarding reads GitHub's "delete head branch on merge" setting. If it is already
  on, nothing is asked. If it is off, ask whether to turn it on, and turn it on only
  after a yes.
- Client-neutral instructions with a per-client mapping, so other clients can be
  added by adding a row.
- Never lose work: git's own refusals are the safety net, plus explicit checks.

## Non-goals

- Deleting remote branches ourselves. GitHub's setting covers every merge; if the
  user declines it, agents leave remote branches alone.
- Sweeping existing stale worktrees or merged branches (this repository's
  `crisp-forest`, the 8 merged local branches) or the old repository's leftovers.
- Cleaning worktrees the agent did not create, or any other session's worktree.
- A helper script, a plugin tool or any plugin-run write command.
- Detecting merges the agent cannot see (see Merge confirmation).
- Codex or Claude Code integration beyond the mapping rows.

## Anti-goals

Failure even if it technically works:

- Removing unmerged, uncommitted or unpushed work, or using `--force` or `-D`.
- Touching the primary checkout's working tree, HEAD or index.
- Doing anything when `worktree_cleanup` is not `true`.
- Claiming cleanup happened without observing it.
- Duplicating what the client already did (a worktree that is already gone).
- A task branch turning cleanup on for itself.

## Approaches

- **A. Skill rule plus an extra hook note — selected.** The skill states the
  client-neutral rule, eligibility checks, order and mapping table; the existing
  `gh pr merge` note gains one sentence when cleanup is on. It leans on git's and the
  client's refusals. Weakness: advisory, so an agent can skip it and the worktree
  simply stays.
- **B. A plus a read-only eligibility script** like `check.py`. More protective,
  but it duplicates what git and the client already refuse.
- **C. A plugin tool that removes the worktree.** Rejected: the plugin never runs
  write commands.

## Settings

A third boolean in the existing `git_workflow` entry (renamed from `pull_requests` on
2026-11-19; the old name is no longer read):

```json
{"git_workflow": {"enabled": true, "agent_merge": true, "worktree_cleanup": false}}
```

- `worktree_cleanup`: absent means off for every effect, but onboarding treats absent
  as "not yet asked" and asks (the preview reports `previous_worktree_cleanup: null`).
  `inspect()` returns `worktree_cleanup: true | false | null`; only `true` turns
  anything on.
- Read and written at the **main project path**, like `agent_merge`: cleanup deletes
  files, so a task branch must not be able to turn it on for itself.
- `enabled: false` turns off all of `git_workflow`, including cleanup.
- Apply requires all three booleans; Smart Router calls and results are unchanged.
- GitHub's setting is not stored in `settings.json`; it lives on GitHub.

## Onboarding

Questions and flow are defined in `skills/vibe-onboard-project/references/procedure.md`
in the existing control-shaped form (header of at most 30 characters, context
starting with the project path, labels of one to five words with descriptions,
"(Recommended)" first or "(Current)" for a saved choice). Changes:

- The opening line becomes **Up to four short questions.**
- **Question 2** keeps its copy except the third choice, now **Skip git workflow
  guidance** — "Saves all of this as off, including cleanup. Run onboarding again to
  change it." Choosing it skips questions 3 and 4.
- **Question 3 — Worktree cleanup.** Header **Worktree cleanup**; question **Clean up
  after a pull request is merged in this project?** Context: "Project: <project
  path>. A worktree is the separate folder an agent works in for one task. Cleanup
  runs only after a merge is confirmed, and never removes unmerged or uncommitted
  work." Choices: **Clean up (Recommended)** — "Removes the task's worktree and its
  local branch after a confirmed merge." and **Keep them** — "Leaves worktrees and
  branches in place. You can remove them yourself."
- **Question 4 — Remote branch cleanup (conditional).** Asked only when
  `git_workflow` stays on **and** one read-only call
  `gh repo view --json nameWithOwner,deleteBranchOnMerge,viewerPermission` succeeds
  **and** `deleteBranchOnMerge` is `false` **and** `viewerPermission` is `ADMIN`.
  Header **Remote branch cleanup**; question **Delete merged branches on GitHub
  automatically?** Context: "Project: <project path>. GitHub, not the agent, can delete
  a pull request's branch as soon as it merges. It is off for <owner/repo>. It applies
  to every merge there, and you can turn it off again in GitHub's settings." Choices:
  **Turn it on (Recommended)** — "GitHub deletes a PR's branch when it merges. Runs gh
  repo edit --delete-branch-on-merge." and **Not now** — "Leaves the setting off." On yes the agent runs
  `gh repo edit <owner/repo> --delete-branch-on-merge`, reads it back with the same
  `gh repo view` call, and reports the result. A failed read-back is reported, not
  claimed. Because that edit changes the whole repository and cancelling afterwards
  cannot undo it, the one-line "Saving:" statement before applying names it ("GitHub:
  turn on auto-delete of merged branches for <owner/repo>"), and a cancel at any
  question stops everything before any change. The edit runs after the settings apply,
  independent of its outcome (the yes is the user's own consent), and the report gives
  each outcome on its own line, so a blocked settings save does not hide a changed
  GitHub setting.
- When the setting is already on, or `gh` is missing, not logged in, not admin, or
  the remote is not on GitHub, question 4 is skipped and the report says why in one
  line (for example "GitHub auto-delete: already on.").
- Report lines gain cleanup: `Git workflow: On · I merge, cleanup on — reminders
  registered; …` (and `Agents merge`; "cleanup on" appears only when on). A
  GitHub line is added when question 4 ran or was skipped for a reason.
- Existing projects saved before this change have no `worktree_cleanup` key, so
  question 3 is asked once; saved choices for the other questions are kept.

## Cleanup rules (in the `vibe-pull-requests` skill)

A new reference `references/worktree-cleanup.md`, linked from the procedure's
"Merging" section. Client-neutral wording; the mapping table below carries the
client-specific part, like Superpowers' "native tool first, git fallback".

**When.** Only when `worktree_cleanup` is `true` at the main project path (read with
the onboarding tool's `git_workflow` preview), and only after the merge is confirmed.

**Merge confirmation** (by observation, never memory): the agent's own `gh pr merge`
result, or the user saying it merged, or finding it merged. Then verify all of:
1. `gh pr view <n> --json state,headRefName,headRefOid,baseRefName` says `MERGED`,
   the base is the repository's default branch, and `headRefName` is the worktree's
   branch; or, without GitHub, `git merge-base --is-ancestor HEAD origin/main` after
   `git fetch origin main`.
2. The worktree's `HEAD` equals `headRefOid` or is an ancestor of `origin/main`, so no
   commits were added after the merge.
3. This is a linked worktree (git dir differs from the common git dir), not the
   primary checkout, and it is the session's own working directory or one the agent
   created in this session. Never another worktree, and never one only found by
   listing worktrees.
4. `git status --porcelain` is empty.

If any check fails or cannot be run, report which and change nothing.

**Order** (verified in scratch repositories on 2026-11-19: git refuses to delete a
branch that a worktree has checked out, and refuses to remove a dirty worktree; and
with `--git-dir=<common git dir>`, `branch -d` checks "merged" against the primary's
HEAD, not the detached worktree's, so it refuses an unmerged branch and a
squash-merged one and deletes a merge-commit one):
1. Before touching anything, tell the user what is about to happen and that the
   session's directory will disappear: "Merge confirmed. Cleaning up now: branch
   <name> and worktree <path>. I will verify from the primary checkout's path
   afterwards."
2. Capture the common git dir (`git rev-parse --git-common-dir`, absolute) and the
   branch name. Check that the primary checkout is on the default branch
   (`git --git-dir=<common git dir> symbolic-ref --short HEAD`); if not, stop, because
   the safe delete would check against the wrong branch.
3. In the worktree: `git switch --detach`.
4. `git --git-dir=<common git dir> branch -d <branch>`. If git refuses (an unmerged
   branch, or a squash or rebase merge, which looks unmerged by design), switch back
   to the branch, report, and stop. Squash and rebase merges are therefore not
   cleaned up; never use `-D` to get around it.
5. Remove the worktree as the **last action that changes anything**, with the client's
   own mechanism, never with force: the table below.
6. Verify with read-only commands that name the primary checkout's path explicitly,
   because the session's own directory is gone: `git -C <primary> worktree list` and
   `git -C <primary> branch --list <branch>`. Report what they show, or say it could
   not be verified. Never claim cleanup from step 5 alone.

Metadata operations that leave the primary checkout's files, HEAD and index alone
(`git --git-dir=… branch -d`, `git worktree remove`, `git -C <primary> worktree list`)
do not count as touching it.

| Client | How to remove the worktree |
|---|---|
| OpenCode | `worktree_remove` with the worktree's directory (needs `edit` permission on it); no `force` unless the user confirms. The docs describe it as removing the worktree only; the branch is already deleted in step 4 and step 6 verifies the result either way. |
| Claude Code | `ExitWorktree` with the remove action; it may already have removed a clean worktree on exit, in which case there is nothing to do |
| Codex | Codex removes its own managed worktrees past a limit; do not remove Codex-managed worktrees. Row unverified: confirm from Codex's official docs while building. |
| Any other client, or no tool | `git worktree remove <path>`, run from outside the worktree and without `--force` |

**Remote branches.** Agents do not delete them. They stop passing `--delete-branch`
to `gh pr merge` unless the user asks (GitHub's setting, if on, does it). A remote
branch is deleted by an agent only on the user's explicit request.

## The hook

The existing `execute.after` case 4 (`gh pr merge`) appends one sentence when
`worktree_cleanup` is `true`: "Cleanup is on: once the merge is confirmed, follow the
worktree cleanup steps in the vibe-pull-requests skill (detach, safe-delete the
branch, remove the worktree last)." With `--auto` it says "once the auto-merge
completes". Nothing else changes; the settings read already happens per match.

## Constraints

- Advisory only; the plugin runs no write command and never calls `gh`. Only the
  agent, following the procedure, runs `gh repo view`, `gh repo edit` and git commands.
- Python 3.11+ standard library and Node ES modules; no new dependencies.
- New exported files go in `scripts/manifest.py`, `package.json` `files` and
  `tests/test_package.py`. README changes go through the `impeccable` skill.
- No real cleanup, `gh repo edit`, install or release happens during development.

## Build slices (smallest valuable first)

1. `project.js`: the `worktree_cleanup` key (null when absent), `previous_worktree_cleanup`,
   apply requires all three booleans; tests first.
2. `onboarding.js` and `index.js`: the tool accepts `worktree_cleanup` for `git_workflow`
   only; tests.
3. Onboarding procedure: Question 2's third choice and skip behavior, Questions 3 and 4,
   the opening line, report lines; the question-shape test counts four questions.
4. Skill: `references/worktree-cleanup.md`, the link from the procedure, the
   `--delete-branch` rule; packaging and tests.
5. Hook: the case 4 sentence; tests.
6. Docs: `PRODUCT.md`, `docs/dependencies.md`, README through `impeccable`.

## Proof expected

- Settings: absent key reads `null` and acts as off; a saved `false` and `true` round
  trip; Smart Router outputs are byte-identical; the worktree copy never turns cleanup
  on when read at the project path.
- Procedure: all four questions pass the shape test; Question 4 text includes its
  conditions and the exact `gh` commands; the Skip choice's effect on Questions 3 and 4
  is stated.
- Skill: the cleanup reference states the order, every check, the mapping rows, the
  no-force rule and the `--delete-branch` rule, and never mentions Superpowers or
  claims enforcement.
- Hook: the case 4 note carries the cleanup sentence only when `worktree_cleanup` is
  `true`; the `--auto` wording; silent when off or absent.
- A test runs the documented git sequence (detach, `--git-dir` safe delete, worktree
  remove) in a temporary repository: an unmerged and a squash-merged branch are
  refused, a merge-commit branch is deleted, the primary's HEAD is unchanged, and a
  dirty worktree is refused.
- Live observation (not unit proof) of a real cleanup and of Question 4 is reported
  separately and only if the user asks for a live run.

## Calls made (the writer's; the user can change any)

- Cleanup covers the worktree **and its local branch** (safe delete).
- The detach, delete-branch, remove-worktree-last order, with the report first.
- Worktree cleanup and remote branch cleanup stay **two questions**. An impeccable
  review proposed one four-choice question, but it needed different choices when
  GitHub's setting is already on (or cannot be read), and it would have mixed what an
  agent removes with what GitHub removes. Two questions keep each question's choices
  fixed and name who acts. Neither is preselected; an explicit choice is the consent.
- Question 2's Skip is relabelled and also skips Questions 3 and 4.
- Question 4 is asked only for an ADMIN on a GitHub repository whose setting is off.
- A missing `worktree_cleanup` key means "ask once", not "no".
- Independent of `agent_merge`: it applies after a merge the user made too, once the
  agent verifies the merge.
- No sweep of existing worktrees or branches.
- Only the session's own worktree (or one created in this session) is eligible.
- Squash and rebase merges are not cleaned up: no `-D`, ever.
- The final report comes before cleanup, and a read-only check from the primary's
  explicit path follows it.
- Question 4's GitHub change is named in the one-line "Saving:" statement and runs
  after, and independent of, the settings apply.
- The Codex mapping row is unverified.

## Left to the builder

The exact wording of report lines beyond the shapes above, how `project.js` adds the
key with the least change, how the shape test parses four questions, the reference
file's layout, and where the procedure links to it.
