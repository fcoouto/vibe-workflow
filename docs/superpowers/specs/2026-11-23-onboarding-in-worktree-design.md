# Onboarding saves in a worktree

## Status and intent

A user onboarded a project from a worktree session and got two settings files: Smart
Router in the worktree's `.vibe-workflow/settings.json` (untracked), git workflow and
session titles committed in the main checkout. Smart Router is read and written per
checkout (`createProjectSettings(directory)`, `index.js:83`); git workflow and session
titles use `atProject: true` (`index.js:238-239`). The split dates from the pull request
workflow design, which made git workflow main-only so a task branch cannot grant itself
merge rights, and kept Smart Router per-worktree "for this feature only". That reason
does not cover Smart Router or titles, and the result is two places to save, one of
which the user must remember to merge.

Decided with the user on 2026-11-23: onboarding always saves in a worktree, every
feature in one file, and the file reaches main by pull request.

## Goals

- One onboarding run writes one file, in the session's worktree, for all three features.
- A session already in a worktree saves there. It does not create another worktree.
- A session in the main checkout gets a worktree and moves into it first.
- A task branch still cannot grant itself merge rights.
- A worktree that lacks entries main already has does not drop them from the file it saves.

## Non-goals

- Enforcing the worktree in code. A primary-checkout session that ignores the procedure can still
  save in main.
- A plugin tool that creates worktrees or moves sessions (the plugin runs no write
  command; see `2026-11-22-cleanup-moves-session-design.md`). The procedure does it with
  OpenCode's own tools.
- Changing the file format, the feature names or the question wording.
- Merging a worktree file that went stale against main. Git's merge handles it.
- Non-OpenCode clients.

## Anti-goals

- Writing to the main checkout's settings file from onboarding.
- Creating a second worktree when the session is already in one.
- A save that silently drops a feature main already has.
- Granting merge rights from an unmerged branch.

## Approaches

- **A. Save in the worktree; reads stay as they are except titles (selected).** Writes move
  to the session's checkout, previews merge main's entries in, git workflow keeps
  reading main, Smart Router and titles read the checkout's own file.
- B. Everything in the main checkout (rejected by the user): needs a direct write to
  main, which skips the pull request and any review.
- C. Everything per worktree, including git workflow reads (rejected): a task branch's
  own `agent_merge: true` would apply to itself before review.
- D. Keep the split and only warn (rejected by the user): the mess stays.

## Behavior

### Where it saves

All three onboarding features write `<root>/.vibe-workflow/settings.json`, where `root`
is the session's checkout top level (`found.root` in `src/project.js`). The tool does not
tell a linked worktree from a primary checkout: in a primary checkout the root is main, so
the save lands in main's file. Keeping the session out of main is the procedure's job
(below). Decided with the user on 2026-11-24: instructions handle that case, code handles
only what instructions cannot (the write path and the overlay).

| Session location | Preview and apply |
|---|---|
| Linked worktree | Save in this worktree. |
| Primary git checkout | Save in main's file. The procedure moves the session to a worktree first, so this is the unguarded case. |
| Not a git repository | Save in the directory. |
| Linked worktree whose main checkout cannot be resolved | Save in this worktree; no overlay. |

The edit permission resource is the root's own `.vibe-workflow/settings.json`, relative to the session directory.

### What a preview and a save see

The effective settings are main's file overlaid entry by entry with the worktree's file:
for each feature, the worktree entry if present, else main's entry. Unknown entries are
preserved, the worktree's winning on a name clash.

- `previous_enabled`, `previous_agent_merge`, `previous_worktree_cleanup` and `change`
  come from the effective settings, so the "(Current)" choices show what the project
  really has.
- Apply writes the effective settings with the chosen feature changed, to the worktree
  file. A worktree with no file therefore gets main's entries plus the new one, and
  merges into main without conflict.
- The overlay applies only when main's file is a different file from the worktree's.
- The snapshot covers both files' contents and the root, so a change to either between
  preview and apply returns `project_settings_changed`.
- When main has no file, the effective settings are the worktree file alone.

### Runtime reads

| Feature | Reads | Why |
|---|---|---|
| `git_workflow` (enabled, `agent_merge`, `worktree_cleanup`) | main checkout, as today | A task branch's file must not grant merge rights. |
| `smart_router` | the checkout's own file, as today | Advisory only. |
| `session_titles` | the checkout's own file (was main) | Advisory only; follows the file onboarding wrote. |

`onboarded()` still counts any saved choice in any of the three reads.

A worktree branched before the settings file was committed has no Smart Router or
titles entry until it rebases on main. That is how Smart Router behaves today. Not
changed here.

### The procedure (`skills/vibe-onboard-project/references/procedure.md`)

- Step 1: before the first preview, the agent checks whether the session's directory is
  a linked worktree (its git directory differs from the git common directory). If it is
  the primary checkout of a git repository, the agent says in one line that it will create
  a worktree and move the chat there, creates it with `worktree_create` (name
  `onboard-vibe-workflow`), moves the session with `session.move`, confirms the working
  directory in a later step (the move applies at the next step boundary, see the cleanup
  spec's evidence) and only then previews. If it cannot, it stops and reports why; it never
  saves in main instead. Not a git repository: no worktree, save in place.
- The opening line stops branching on `root` differing: every run names the worktree.
- Question 5's and the Saving line's "saved in the main checkout" wording goes; the
  Saving line names the worktree file and says committing and merging it is the user's
  step.
- Final report: one line for the file, then **Git workflow and cleanup take effect only
  after this branch is merged into main.** Smart Router and session titles apply in this
  worktree now and everywhere after the merge. Offer a pull request through
  `vibe-pull-requests`; do not merge unasked.
- The "never write the settings file yourself" rule stays.

## Changes

- `src/project.js`: a `worktree` option: an instance that saves at the session root and
  previews from the effective settings (main overlaid by the worktree file), and
  snapshots both files. Existing
  read instances keep their behavior; `session_titles` reads switch to the own-checkout
  instance.
- `src/onboarding.js`: use that instance for all three features; permission resource
  unchanged in form (relative to the session directory).
- `index.js`: `sessionTitles` is created without `atProject`; onboarding gets the
  saving instances.
- `skills/vibe-onboard-project/references/procedure.md`: as above.
- `README.md` line about where choices are saved: update only through the `impeccable`
  skill (`AGENTS.md`).
- `docs/superpowers/specs/2026-11-19-pull-request-workflow-design.md`: add a note under
  "Read and written at the main project path": writes moved to the worktree on
  2026-11-23, reads unchanged.
- `docs/development.md`: add what is known after the first live run.

## Tests (written first)

In `tests/plugin/project.test.js` and `tests/plugin/onboarding.test.js`:

- From a linked worktree, saving any of the three features writes only the worktree file;
  main's file is untouched. This replaces `git_workflow apply from a linked worktree
  writes the main checkout only`.
- In a primary git checkout the instance saves at the root, with no overlay.
- A non-git directory still saves in place.
- A worktree with no file, main holding `git_workflow` and `session_titles`: the preview
  shows them as current; applying Smart Router writes all three entries.
- The reported messy case: worktree file with `smart_router` only, main with the other
  two: saving any feature writes all three, with main's values for the others.
- Unknown entries in either file survive; the worktree wins a clash.
- Changing either file between preview and apply returns `project_settings_changed`.
- Runtime: `git_workflow` still reads main when the worktree file says `agent_merge:
  true` (existing test `a worktree copy never grants merge` stays green); `session_titles`
  reads the checkout's own file.
- Procedure text contains the worktree check and move step, the new final-report line,
  and no "saved in the main checkout" claim. The existing wording tests at
  `onboarding.test.js:264` and `:314` change with it.

## Known gaps

- The agent steps in step 1 (create, move, confirm) rest on `worktree_create` and
  `session.move`, observed on 2.2.6 in the cleanup spec; not tried here.
- A stale worktree file (main changed after branching) can conflict at merge. Git shows
  it; onboarding does not detect it.
- Session titles and Smart Router do not apply in a worktree branched before the file
  was committed.

## What is left to the builder

Instance API shape in `src/project.js`, the exact snapshot encoding, the blocked-reason
text mapping in the procedure, and the worktree name collision case (`worktree_create`
fails: choose another name).
