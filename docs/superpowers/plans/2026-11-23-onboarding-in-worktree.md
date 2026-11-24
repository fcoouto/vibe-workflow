# Onboarding saves in a worktree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Onboarding saves all three features in one file at the session's checkout root, seeded from main's file, and the procedure moves a primary-checkout session into a worktree first. Git workflow reads stay at main.

**Architecture:** `src/project.js` gains a `worktree` option: the instance saves at the session root and previews from main's file overlaid entry by entry with the worktree's file. Code does not guard the primary checkout; the procedure does. `index.js` builds those instances for onboarding and reads `session_titles` from the checkout's own file. The procedure creates and enters a worktree when blocked.

**Tech Stack:** Node ES modules, `node:test`. No new dependencies or files.

**Spec:** `docs/superpowers/specs/2026-11-23-onboarding-in-worktree-design.md`

## Global Constraints

- Settings file: `.vibe-workflow/settings.json`, `{ "version": 1, "features": [{ "<name>": {...} }] }`; features `smart_router`, `git_workflow`, `session_titles`.
- Onboarding never writes the main checkout's file; the "never write the settings file yourself" rule stays.
- `git_workflow` runtime reads stay `atProject: true`. A worktree file never grants `agent_merge` or `worktree_cleanup`.
- Existing instances (no `worktree` option) keep their results and snapshots byte-for-byte.
- No `worktree_required` block (decided 2026-11-24). A primary checkout saves at its root with no overlay; non-git directories and unresolved linked worktrees save in place.
- Layout (`AGENTS.md`): no new files. Plugin tests in `tests/plugin/*.test.js`, procedure in `skills/vibe-onboard-project/references/procedure.md`.
- `README.md` changes only through the `impeccable` skill.
- Write tests first, validate, commit; commands in `AGENTS.md`. Local only: no push, no PR without the user.

## Review Focus

- Main's file is invalid or unsafe while the worktree's is fine: blocked `project_settings_invalid`, nothing written. (Task 1)
- Worktree has no file and the answer equals main's value: `change: "none"`, no file created. (Task 1)
- Session in a subfolder of a worktree: writes at the worktree's top level, permission resource relative to the session directory. (Task 2)
- Main's file changes between preview and apply: `project_settings_changed`. (Task 1)

---

### Task 1: `worktree` mode in `src/project.js`

**Files:**
- Modify: `src/project.js` (`findRoot`, `snapshot`, `createProjectSettings`)
- Test: `tests/plugin/project.test.js`

**Interfaces:**
- Produces: `createProjectSettings(directory, feature, { atProject?: boolean, worktree?: boolean })`. `worktree: true` with `atProject: true` throws `Error("unsupported options")`. Results keep their shape; `path` is the root's file, `project` the main checkout path.

- [ ] **Step 1: Write failing tests** in `tests/plugin/project.test.js` (git fixtures follow the existing `gitIn` pattern; `create(dir, feature, { worktree: true })`):
  - `worktree mode saves only the worktree file`: linked worktree, main file `{git_workflow}`; apply `smart_router`; worktree file exists, main file bytes unchanged.
  - `worktree mode in a primary checkout saves at the root with no overlay`.
  - `worktree mode saves in place outside git` and `in a linked worktree whose main cannot be resolved` (reuse the bare-repository fixture).
  - `worktree mode seeds from main`: no worktree file, main `[git_workflow, session_titles]` on; preview `smart_router` → `previous_enabled: null`, `change: "create"`; apply writes `[git_workflow, session_titles, smart_router]` with main's values.
  - `worktree mode reads main's value as current`: main has `session_titles` on; preview `session_titles` with `true` → `previous_enabled: true`, `change: "create"` (the checkout has no file, so main's choices are materialized; changed after review 2026-11-24); apply → `saved`; a second preview is `none`.
  - `worktree mode completes a partial worktree file`: worktree `[smart_router]`, main `[git_workflow, session_titles]`; apply `git_workflow` changed → worktree file `[git_workflow(new), session_titles, smart_router]`.
  - `worktree mode keeps unknown entries and top-level keys from both files, the worktree winning a clash`.
  - `worktree mode snapshot covers main's file`: change main's file between preview and apply → `project_settings_changed`; unchanged main → `saved`.
  - `worktree mode blocks an invalid main file`: `project_settings_invalid`, nothing written.
  - `worktree with atProject throws unsupported options`.

- [ ] **Step 2: Run** `node --test tests/plugin/project.test.js`. Expected: the new tests FAIL, old ones pass.

- [ ] **Step 3: Implement.**
  - `current()` in worktree mode: `root = found.root`; the main file is read with `readSettings` from `found.project` unless `found.unresolved` or `found.project === root`. A read failure on either file gives `project_settings_invalid`.
  - Effective data: `features` = main's entries in main's order, each replaced by the worktree's entry of the same name, then worktree-only entries in the worktree's order; top-level value is `{ ...main.value, ...own.value, version: 1, features }`; recompute `featureIndex` by name; keep `raw` as the worktree's raw and add `mainRaw`.
  - `snapshot(root, raw, mainRaw)`: when `mainRaw` is passed (worktree mode, even when absent, pass `null`) add a `\0main\0present|absent\0` section; the existing calls stay unchanged.
  - `preview`: `change` is `"none"` when the values equal the effective ones, else `"create"` when there is no worktree raw, else `"update"` (reorder the current expression; behavior for existing instances is identical).
  - `apply`: write the effective value to the worktree file as today; `numbersRoundTrip` checks both raws.

- [ ] **Step 4: Run** `node --test tests/plugin/project.test.js`. Expected: all PASS.

- [ ] **Step 5: Commit** `feat: add a worktree mode to project settings`.

### Task 2: Wire onboarding and runtime reads

**Files:**
- Modify: `index.js:238-252`; `src/onboarding.js` only if a signature needs it (the tool already takes `projects`)
- Test: `tests/plugin/onboarding.test.js`, `tests/plugin/plugin.test.js`

**Interfaces:**
- Consumes: Task 1's `{ worktree: true }`.
- Produces: onboarding `projects` built with `{ worktree: true }` for all three features (the Smart Router one keeps the `notices.clear()` wrapper); `sessionTitles` read instance without `atProject`.

- [ ] **Step 1: Update the onboarding fixture** in `tests/plugin/onboarding.test.js`: after `git init`, commit an empty commit, add a linked worktree `join(root, "linked")`, create `nested` inside it, and use it as the session directory. Fixture returns `path` (the worktree file), `mainPath` and `writeMain(value)` beside `write`; add option `primary: true` that keeps the old primary-checkout session. Replace `git_workflow apply from a linked worktree writes the main checkout only` with the worktree-writes test below.

- [ ] **Step 2: Write failing tests:**
  - `all three features save only in the worktree`: apply each feature; main's file absent; `assertCalls` resources `["../.vibe-workflow/settings.json"]`.
  - `a primary checkout saves at its root` (with `primary: true`).
  - `the split case saves one complete file`: worktree `[smart_router]`, main `[git_workflow, session_titles]`; applying `smart_router` leaves all three entries in the worktree file.
  - In `plugin.test.js`, `session titles follow the checkout's own file`, copying the nearest titles registration test: own file with titles on and no main file registers; main file on and no own file does not.

- [ ] **Step 3: Run** `node --test "tests/plugin/*.test.js"`. Expected: new tests FAIL; the fixture change makes some old tests fail on `root`/`path`; fix those assertions to the new fixture values (the worktree top-level, `path`).

- [ ] **Step 4: Implement** in `index.js`: a `saving = (feature) => createProjectSettings(directory, feature, { worktree: true })`; pass `saving` instances to `registerOnboarding` (Smart Router wrapper over `saving("smart_router")`); `sessionTitles = createProjectSettings(directory, "session_titles")`.

- [ ] **Step 5: Run** `node --test "tests/**/*.test.js"` then `python3 -B scripts/validate.py`. Expected: PASS.

- [ ] **Step 6: Commit** `feat: onboarding saves in the session's worktree`.

### Task 3: The procedure

**Files:**
- Modify: `skills/vibe-onboard-project/references/procedure.md` (lines 86-92, 126, 135-138, 182-185)
- Test: `tests/plugin/onboarding.test.js` (the two text tests at the `saved in the main checkout` and `When \`root\` differs` assertions)

- [ ] **Step 1: Update the text tests first.** Remove `"saved in the main checkout"`, `"add that Smart Router is saved in this worktree"`, and the two `When root differs` phrases. Assert the new text includes: `worktree_create`, `session.move`, `onboard-vibe-workflow`, `Git workflow and cleanup take effect only after this branch is merged into main.`, and that it does not include `saved in the main checkout`. Run the file; expected: FAIL.

- [ ] **Step 2: Edit the procedure.**
  - Step 1: before the first preview, check whether the session directory is a linked worktree (`git rev-parse --absolute-git-dir --git-common-dir` differ). If it is the primary checkout of a git repository, say in one line that a worktree is being created and the chat moves there; call `worktree_create` with `onboard-vibe-workflow` (another name if it exists); move the session with `session.move` to the returned directory; confirm the working directory in a later step (the move applies at the next step boundary); then preview. If any part fails, stop and report; never save in main instead. Not a git repository: save in place.
  - Opening line: names the worktree every run; drop the `root differs` branching.
  - Saving line: names the worktree file and says committing and merging it is the user's step; drop the "main checkout" wording for git workflow and session titles.
  - Final report: one file line, then **Git workflow and cleanup take effect only after this branch is merged into main.** Offer a pull request through `vibe-pull-requests`; do not merge unasked. Replace the old "saved only in this worktree" line.
  - Question 5 reference to `project_root_unavailable` handling: keep the reason, drop "run from the main checkout".

- [ ] **Step 3: Run** `node --test tests/plugin/onboarding.test.js`. Expected: PASS.

- [ ] **Step 4: Commit** `docs: onboarding procedure creates and uses a worktree`.

### Task 4: Docs and review

**Files:**
- Modify: `docs/superpowers/specs/2026-11-19-pull-request-workflow-design.md:92-95` (add a note: writes moved to the worktree on 2026-11-23, reads unchanged); `docs/development.md` (add "Onboarding in a worktree: what is known": tests only, live run not done); `README.md:40-41` through the `impeccable` skill only.

- [ ] **Step 1: Make the two doc edits.** Run `python3 -B scripts/validate.py`; expected PASS.
- [ ] **Step 2: Update `README.md`** with the `impeccable` skill: all choices save in a worktree; git workflow and cleanup apply after the merge.
- [ ] **Step 3: Run all commands in `AGENTS.md`** (unittest, node tests, validate, package). Expected: PASS.
- [ ] **Step 4: Commit** `docs: record onboarding in a worktree`, then one fresh independent reviewer on the whole branch for behavior and security (`AGENTS.md`). Report unavailable checks honestly. No push or PR without the user.
