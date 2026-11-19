# Post-merge cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `worktree_cleanup` choice under `git_workflow`, two new onboarding questions (worktree cleanup and a conditional GitHub remote-branch question), and client-neutral cleanup rules with a one-sentence hook reminder.

**Architecture:** `project.js` gains a third `git_workflow` boolean (null when unasked, false/true when answered). The onboarding tool and procedure carry the new questions; the agent, not the plugin, runs every `gh` and git command. A new skill reference holds the verified cleanup order and a per-client mapping. The existing `gh pr merge` hook note gains one sentence when cleanup is on.

**Tech Stack:** Node ES modules, `node --test`, Python `unittest` for packaging.

**Spec:** `docs/superpowers/specs/2026-11-19-post-merge-cleanup-design.md`

## Global Constraints

- Advisory only. The plugin never runs a write command and never calls `gh`; only the agent, following the procedure, runs `gh repo view`, `gh repo edit` and git commands.
- Settings: `{"git_workflow": {"enabled": <bool>, "agent_merge": <bool>, "worktree_cleanup": <bool>}}`, booleans only. `worktree_cleanup` absent means **not yet asked** (`null` from `inspect()` and `preview()`) and **off** for every effect; only `true` turns anything on.
- `git_workflow` is read and written at the main project path, so a worktree copy never turns cleanup on. `enabled: false` turns off all of `git_workflow`, including cleanup.
- For `git_workflow`, apply requires all three booleans (`enabled`, `agent_merge`, `worktree_cleanup`). Smart Router calls and results stay byte-identical; existing Smart Router tests are not edited.
- `index.js` needs no change (settings results flow through `inspect()`).
- Every task's final run includes `python3 -B scripts/validate.py`.
- Cleanup never uses `--force`, `-D` or `--delete-branch`, never touches the primary checkout's files, HEAD or index, and never removes unmerged, uncommitted or unpushed work.
- Onboarding questions keep the control-shaped form: header of at most 30 characters, context starting with `Project: <project path>.`, two or three choices with labels of one to five words and one-line descriptions without backticks, first choice marked "(Recommended)" (or the saved choice "(Current)").
- Skill files never mention Superpowers and never say "enforce" except in "not enforced". README edits go through the `impeccable` skill and stay short.
- Python 3.11+ standard library and Node ES modules only. New exported files go in `scripts/manifest.py`, `package.json` `files` and `tests/test_package.py`.
- No real cleanup, no `gh repo edit` and no release happens during development.

## Review Focus

- A project file without `worktree_cleanup` reads `null`, acts as off, is asked once by onboarding, and never triggers the hook sentence. Pinned in Tasks 1 and 5.
- A worktree copy saying `worktree_cleanup: true` never turns cleanup on when read at the project path. Pinned in Task 1.
- The documented git sequence behaves as written in a real temporary repository with a bare `origin`: a never-pushed unmerged branch and a branch with a local commit beyond its upstream are refused; a pushed unmerged or squash-merged branch is deleted while its upstream exists; a squash-merged branch is refused once its remote branch is deleted and pruned; a merge-commit branch is deleted; the primary's HEAD is unchanged; a dirty worktree is refused. Pinned in Task 4.
- Skipping the pull request question skips Questions 3 and 4; Question 4's conditions, exact `gh` commands, read-back and apply order are stated; a yes to Question 4 is a change even when the three settings are unchanged; a cancel or a denied file-edit permission means no GitHub change. Pinned in Task 3.
- The cleanup reference reads the saved choice (`previous_enabled` and `previous_worktree_cleanup`), not the preview's proposed values. Pinned in Task 4.
- The hook sentence appears only when `worktree_cleanup` is `true`, with the `--auto` wording, and never for `false`, `null` or absent. Pinned in Task 5.

---

### Task 1: The `worktree_cleanup` setting

**Files:**
- Modify: `project.js`
- Test: `tests/project.test.js`

**Interfaces:**
- Produces: `FEATURES.git_workflow = ["enabled", "agent_merge", "worktree_cleanup"]`. For `git_workflow`, `inspect()` and `preview()` add `worktree_cleanup: boolean | null` (`null` when there is no file, no entry, or no such key); `preview()` also adds `previous_worktree_cleanup` and proposes `asked.worktree_cleanup ?? saved ?? false`. `change` is `"none"` only when `enabled`, `agent_merge` **and** `worktree_cleanup` all equal the saved values, with a `null` saved value counting as different, so answering `false` once persists the key.
- Produces: `apply(values, snapshot, options)` for `git_workflow` requires booleans for all three keys (otherwise `{ state: "blocked", reason: "invalid_request" }`), writes all three, keeps unknown keys in the entry, and returns them in `saved` and `unchanged` results; blocked results carry `worktree_cleanup: null`. Smart Router outputs gain nothing.

- [ ] **Step 1: Write failing tests** in `tests/project.test.js`: update the existing `git_workflow` `apply(...)` calls that send two booleans (about eight call sites, including the invalid-values loop and the `unchanged` checks) and the saved-file `deepEqual` assertions (three) so they include `worktree_cleanup`, then add `worktree_cleanup: "yes"` giving `project_settings_invalid` when read from a file, and add `a missing worktree_cleanup key reads null and previews false with an update` (file `{git_workflow:{enabled:true,agent_merge:true}}`: `inspect()` has `worktree_cleanup: null`; `preview()` has `previous_worktree_cleanup: null`, `worktree_cleanup: false`, `change: "update"`; applying `false` writes the key and a second preview is `"none"`), `worktree_cleanup round-trips true and false and keeps unknown keys`, `git_workflow apply requires all three booleans` (each missing or non-boolean value gives `invalid_request`), `unchanged compares worktree_cleanup too`, `a worktree copy never turns cleanup on at the project path` (as the existing self-grant test, with `worktree_cleanup: true` in the worktree file and `false` in the main file), and `Smart Router results have no worktree_cleanup key`.
- [ ] **Step 2: Run** `node --test tests/project.test.js`. Expected: new tests fail; unchanged tests pass.
- [ ] **Step 3: Implement** the key in `FEATURES`, `current()` (`entry ? entry.worktree_cleanup ?? null : null`), `preview`, `apply` and the written entry, with the least change to the existing `agent_merge` handling.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py` and `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: add the worktree_cleanup setting`.

### Task 2: The onboarding tool accepts it

**Files:**
- Modify: `onboarding.js`
- Test: `tests/onboarding.test.js`

**Interfaces:**
- Consumes: Task 1.
- Produces: the tool input gains `worktree_cleanup: boolean` (schema property and allowed key). It is valid only with `feature: "git_workflow"` (otherwise `invalid_request`); preview passes it through as a value; apply for `git_workflow` requires it as a boolean. Results are the settings results, so they carry `worktree_cleanup` and `previous_worktree_cleanup`. Readiness is unchanged.

- [ ] **Step 1: Write failing tests**: update the existing `git_workflow` apply calls in `tests/onboarding.test.js` that go through the `prefs(...)` helper (about eight, including the permission-unavailable and denial tests, which must keep their current expectations) and the saved-file `deepEqual` assertions (three) so they send and expect `worktree_cleanup`, then add `worktree_cleanup with the default feature is invalid`, `git_workflow apply without worktree_cleanup is blocked`, `preview reports previous_worktree_cleanup null for a file saved before this feature`, and `an applied worktree_cleanup true is saved and read back`.
- [ ] **Step 2: Run** `node --test tests/onboarding.test.js`. Expected: new tests fail.
- [ ] **Step 3: Implement** the schema property, the allowed-key list, the `agent_merge`-style validation for `worktree_cleanup`, the value pick and the apply check.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py`, `node --test tests/*.test.js` and `python3 -B -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: let onboarding save worktree_cleanup`.

### Task 3: The onboarding procedure

**Files:**
- Modify: `skills/vibe-onboard-project/references/procedure.md`
- Test: `tests/onboarding.test.js`

**Interfaces:**
- Consumes: Task 2.
- Produces, in the existing field form: the opening line **Up to four short questions.**; Question 2's third choice becomes **Skip git workflow guidance** — "Saves all of this as off, including cleanup. Run onboarding again to change it." and skips Questions 3 and 4; **Question 3** (header **Worktree cleanup**; question **Clean up after a pull request is merged in this project?**; choices **Clean up (Recommended)** — "Removes the task's worktree and its local branch after a confirmed merge." and **Keep them** — "Leaves worktrees and branches in place. You can remove them yourself."); **Question 4** (header **Remote branch cleanup**; question **Delete merged branches on GitHub automatically?**; choices **Turn it on (Recommended)** — "GitHub deletes a PR's branch when it merges. Runs gh repo edit --delete-branch-on-merge." and **Not now** — "Leaves the setting off."), with context text from the spec.
- The steps state: Question 3 shows a saved non-null `previous_worktree_cleanup` as "(Current)"; Question 4 runs only when `git_workflow` stays on and `gh repo view --json nameWithOwner,deleteBranchOnMerge,viewerPermission` succeeds with `deleteBranchOnMerge` false and `viewerPermission` `ADMIN`, and otherwise is skipped with a one-line report (for example `GitHub auto-delete: already on.`); the "Saving:" line names the GitHub change; settings apply first, then `gh repo edit <owner/repo> --delete-branch-on-merge` runs on a yes, independent of the apply outcome, and is read back with the same `gh repo view` call; a cancel at any question or a denied native file-edit permission means no GitHub change, and the "Saving:" line comes before the settings apply; a yes to Question 4 counts as a change, so "Nothing to change." applies only when no GitHub change is pending; every tool call sends all three booleans (Skip sends all `false`); the existing report lines stay for cleanup off and the "cleanup on" variants are added; report lines read `Git workflow: On · I merge, cleanup on — …` (and `Agents merge`; "cleanup on" only when on) plus one GitHub line when Question 4 ran or was skipped for a reason.

- [ ] **Step 1: Write failing tests** in `tests/onboarding.test.js`: change the shape test to four questions (first choice "(Recommended)", context starting `Project: <project path>.`, labels of at most five words, no backticks in choices) and add a flow test with the new phrases: `Up to four short questions.`, `Skip git workflow guidance`, `gh repo view --json nameWithOwner,deleteBranchOnMerge,viewerPermission`, `gh repo edit`, `--delete-branch-on-merge`, `GitHub auto-delete: already on.`, `cleanup on`, `"worktree_cleanup"`, `previous_worktree_cleanup`, `ADMIN`, `deleteBranchOnMerge`, `GitHub: turn on auto-delete of merged branches`, the read-back with the same `gh repo view`, `Nothing to change` together with the Question 4 exception, a cancel or denied permission meaning no GitHub change, and the other skip lines (not admin, `gh` missing or not logged in, not on GitHub); assert the Question strings of Questions 3 and 4 in the shape test as for the first two; keep the existing report-line pins; remove the old "Two short questions." pin.
- [ ] **Step 2: Run** `node --test tests/onboarding.test.js`. Expected: failures for the new text.
- [ ] **Step 3: Implement** the procedure text above.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py`, `node --test tests/*.test.js` and `python3 -B -m unittest discover -s tests`. Expected: all pass.
- [ ] **Step 5: Commit** `docs: ask about worktree and remote branch cleanup during onboarding`.

### Task 4: The cleanup rules and their proof

**Files:**
- Create: `skills/vibe-pull-requests/references/worktree-cleanup.md`, `tests/worktree-cleanup.test.js`
- Modify: `skills/vibe-pull-requests/references/procedure.md`, `scripts/manifest.py`, `tests/test_package.py`, `tests/pull-request-skill.test.js`

**Interfaces:**
- The reference states, in client-neutral wording: when (only if `worktree_cleanup` is `true`, read through the onboarding tool's `git_workflow` preview, and after the merge is confirmed); the saved-choice read (`previous_enabled` and `previous_worktree_cleanup` both `true`); the four confirmation checks (`gh pr view <n> --json state,headRefName,headRefOid,baseRefName` with the default branch from `gh repo view --json defaultBranchRef` or `git symbolic-ref --short refs/remotes/origin/HEAD`, or `git merge-base --is-ancestor HEAD origin/main` after `git fetch origin main`; `HEAD` equals `headRefOid` or is an ancestor of `origin/main`; a linked worktree that is the session's own directory or one created in this session, never the primary or another worktree; empty `git status --porcelain`); the six-step order from the spec (report first; capture the common git dir and branch and check the primary is on the default branch; `git switch --detach`; `git --git-dir=<common git dir> branch -d <branch>`, switching back and stopping if refused, never `-D`; stating that `-d` is only a backstop that refuses a branch holding commits that exist nowhere else, and that the four checks decide whether the pull request merged; remove the worktree as the last action that changes anything, never with force; verify with read-only `git -C <primary>` commands); the note that metadata operations do not count as touching the primary checkout; the mapping table (OpenCode `worktree_remove`, Claude Code `ExitWorktree`, Codex left to manage its own and marked unverified, any other client `git worktree remove <path>`); and that agents never delete remote branches and stop passing `--delete-branch` unless the user asks.
- `procedure.md` links to it from "Merging" and adds the `--delete-branch` rule. `scripts/manifest.py` and `tests/test_package.py` list the new file.

- [ ] **Step 1: Write failing tests**: in `tests/pull-request-skill.test.js` add `the cleanup reference states the order, checks, mapping and the no-force rules` (phrases `worktree_cleanup`, `git switch --detach`, `--git-dir`, `branch -d`, `last action that changes anything`, `git -C`, `worktree_remove`, `ExitWorktree`, `git worktree remove`, `--delete-branch`, `merge-base --is-ancestor`, `previous_worktree_cleanup`, `previous_enabled`, `defaultBranchRef`, and that every line containing `-D` also contains "never"), include the new file in the existing no-Superpowers and no-"enforce" loop, and check the procedure links to it; in `tests/worktree-cleanup.test.js` add `the documented git sequence behaves as written in a temporary repository` (use `mkdtemp` under the OS temp dir, removed in `t.after`; strip `GIT_*` from the environment and set `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `-c user.name=… -c user.email=… -c commit.gpgsign=false -c core.hooksPath=/dev/null`, `init -b main`; a local bare `origin` with the primary's `main` pushed; linked worktrees each with one commit: never pushed and unmerged; pushed (`push -u`) and unmerged; merged with `--no-ff`; squash-merged and pushed; squash-merged and pushed then the remote branch deleted and `fetch --prune`; pushed plus one extra local commit; for each, from inside its worktree run `git switch --detach` then `git --git-dir=<common> branch -d`: the never-pushed unmerged one and the one with a local commit beyond its upstream are refused; the pushed unmerged one and the squash-merged one with an upstream are deleted; the pruned squash-merged one is refused; the merge-commit one is deleted; the primary's HEAD is still `main` after each; `git worktree remove` of a worktree with an untracked file is refused and succeeds once it is clean); update `tests/test_package.py`'s expected file set.
- [ ] **Step 2: Run** `node --test tests/*.test.js` and `python3 -B -m unittest tests.test_package`. Expected: new tests fail (the file is missing).
- [ ] **Step 3: Implement** the reference, the procedure link and rule, and the manifest entry.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: add the worktree cleanup rules`.

### Task 5: The hook sentence

**Files:**
- Modify: `pr-advice.js`
- Test: `tests/pr-advice.test.js`, `tests/pr-hook.test.js`

**Interfaces:**
- `createAdvisor` reads `state.worktree_cleanup === true`; `mergeNote(auto, agentMerge, primaryPath, cleanup)` appends, when `cleanup` is true, "Cleanup is on: once the merge is confirmed, follow the worktree cleanup steps in the vibe-pull-requests skill (detach, safe-delete the branch, remove the worktree last)." With `auto`, "once the merge is confirmed" becomes "once the auto-merge completes". Nothing else changes.

- [ ] **Step 1: Write failing tests**: in `tests/pr-advice.test.js` add `case 4 adds the cleanup sentence only when worktree_cleanup is true` (true: present; false, null and absent: absent; `true` with a state that is not `on`: no note at all), `case 4 with auto waits for the auto-merge to complete`, and `both the user-merges and cleanup sentences appear together`; in `tests/pr-hook.test.js` add `a settings file with worktree_cleanup true adds the cleanup sentence to a gh pr merge result` and `a file without the key adds nothing`.
- [ ] **Step 2: Run** `node --test tests/pr-advice.test.js tests/pr-hook.test.js`. Expected: new tests fail.
- [ ] **Step 3: Implement** the sentence and the state read.
- [ ] **Step 4: Run** `python3 -B scripts/validate.py` and `node --test tests/*.test.js`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: remind agents about cleanup after a merge`.

### Task 6: Docs and final package check

**Files:**
- Modify: `PRODUCT.md`, `docs/dependencies.md`, `README.md`, `docs/superpowers/specs/2026-11-19-pull-request-workflow-design.md`

- [ ] **Step 1: Update** `PRODUCT.md` (capabilities: post-merge cleanup is opt-in, advisory, covers the local worktree and branch; remote branch cleanup is GitHub's setting, offered during onboarding), `docs/dependencies.md` (the new reference and its source, the earlier plugin's post-merge cleanup; no helper script or runtime carried over), and the pull request design spec's Settings section (one line pointing to the cleanup design and the new key).
- [ ] **Step 2: Update** `README.md` through the `impeccable` skill: one or two lines saying onboarding also asks about cleanup after a merge and, if GitHub's setting is off, offers to turn it on.
- [ ] **Step 3: Run** `python3 -B scripts/validate.py`, `python3 -B -m unittest discover -s tests`, `node --test tests/*.test.js`, then `python3 -B scripts/package.py --output /private/tmp/vw-<random>` and `python3 -B scripts/validate.py --root` on it. Expected: all pass.
- [ ] **Step 4: Commit** `docs: record post-merge cleanup`.

## After the tasks

Get an independent review of the whole branch (AGENTS.md requires it), then open the PR through the `git_workflow` lifecycle. Report unit proof and the scratch-repository test separately; no real cleanup, `gh repo edit` or release happens in this change.
