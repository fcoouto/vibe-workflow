# Cleanup Without Asking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents clean up merged worktrees (their own and older ones) without the user asking, deleting only tool caches on their own, never before the merge, and asking about real work before `[READY]`.

**Architecture:** One new plugin module, `src/merged-worktrees.js`, finds merged worktrees with two or three local read-only git calls under a 500 ms deadline and formats one note. The existing `gh pr ready` / `[READY]` / `gh pr merge` notes (`src/pr-advice.js`) and the `using-git-worktrees` skill note (`src/skill-advice.js`) append it. Everything that deletes stays agent guidance in `skills/vibe-pull-requests/references/worktree-cleanup.md`.

**Tech Stack:** Node ES modules (`node:child_process`, `node:test`), Python 3.11+ standard library for packaging checks. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-27-cleanup-without-asking-design.md`

## Global Constraints

- Plugin: read-only git, no `gh`, no network, no write command, no cache, no background work, no new dependencies.
- Reads run only at three moments: after a successful `gh pr ready` (not `--undo`) or retitle to `[READY]`, after a successful `gh pr merge` without `--auto`, and when `using-git-worktrees` loads. Only when `state` is `"on"` and `worktree_cleanup === true`.
- One shared 500 ms deadline for all reads; on timeout or any failure the note is omitted and existing text is unchanged.
- Rebuildable list (exact): `node_modules/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.DS_Store`.
- Nothing is deleted, moved or archived before the merge is confirmed.
- Archive folder: `<primary checkout>/.vibe-workflow/.archives/<branch>/`, self-ignoring via `.vibe-workflow/.archives/.gitignore` containing `*`.
- Never `--force`, `-D`, or `worktree_remove` with `force`.
- Settings unchanged; `worktree_cleanup` stays the only switch.
- New shipped files go in `package.json` `files`, `scripts/manifest.py` and `tests/tooling/test_package.py` in the same change. README changes only through `impeccable` (none planned).
- No real cleanup of this repository's worktrees during development.

## Review Focus

1. `refs/remotes/origin/HEAD` missing (a clone without it): the reads fail and the note is silent; the existing merge note is unchanged. Test in Task 1.
2. A worktree path with spaces: parsed whole and named whole in the note. Test in Task 1.
3. A prunable worktree (folder moved or gone): still named, flagged `needs repair`, never silently dropped. Test in Task 1.
4. More than 10 merged worktrees: 10 paths then `and N more`. Test in Task 1.
5. The calling session sits in a merged worktree and the host's session list gives no directories: that worktree is still excluded through the event's own directory. Test in Task 1 and Task 2.

---

### Task 1: The merged-worktree finder and its note

**Files:**
- Create: `src/merged-worktrees.js`
- Modify: `src/session-titles.js:135` (use a shared `isRunning`)
- Modify: `package.json` (`files`), `scripts/manifest.py:9`, `tests/tooling/test_package.py:103`
- Test: `tests/plugin/merged-worktrees.test.js`

**Interfaces:**
- Produces:
  - `export function isRunning(time, started): boolean` in `src/session-titles.js`, same predicate as today's inner `running` (`finite(time?.busy) && time.busy >= started && !(finite(time.idle) && time.idle >= time.busy)`); the inner `running` becomes `(time) => isRunning(time, started)`.
  - `export function parseWorktrees(porcelain: string): Array<{ path: string, head: string|null, branch: string|null, locked: boolean, prunable: boolean }>` — `branch` is the short name (`refs/heads/` stripped) or `null` when detached.
  - `export function createSweep({ primary, run = defaultRun, busy = async () => null, deadline = 500 }): { note({ exclude }: { exclude?: string|null }): Promise<string|null> }`
    - `primary: async () => string|null` (the primary checkout path; existing `primary` helper in `index.js`).
    - `run: async (cwd: string, args: string[]) => { code: number, stdout: string }` — never throws for a nonzero exit; throws only when git cannot start.
    - `busy: async () => Set<string>|null` — directories of sessions running now; `null` when unknown.
  - `export function busyDirectories(ctx, started): () => Promise<Set<string>|null>` — one `ctx.session.list({ limit: 50, order: "desc" })` page; keeps items where `isRunning(item.time, started)`; returns their `location.directory` values; returns `null` if the call fails or no running item has a string `location.directory`.

- [ ] **Step 1: Write the failing tests** in `tests/plugin/merged-worktrees.test.js`. A fake `run` answers by `args.join(" ")`. Fixture porcelain:

```js
const PORCELAIN = [
  "worktree /repo", "HEAD a1", "branch refs/heads/main", "",
  "worktree /wt/merged one", "HEAD b2", "branch refs/heads/feat/one", "",
  "worktree /wt/open", "HEAD c3", "branch refs/heads/feat/open", "",
  "worktree /wt/detached", "HEAD d4", "detached", "",
  "worktree /wt/locked", "HEAD e5", "branch refs/heads/feat/locked", "locked", "",
  "worktree /wt/moved", "HEAD f6", "branch refs/heads/feat/moved", "prunable gitdir file points to non-existent location", "",
].join("\n")
// for-each-ref --merged refs/remotes/origin/HEAD prints: main, feat/one, feat/locked, feat/moved
// merge-base --is-ancestor d4 refs/remotes/origin/HEAD exits 0
```

Tests and assertions:
- `parseWorktrees reads paths with spaces, branches, detached, locked and prunable` — `parseWorktrees(PORCELAIN)[1]` deep-equals `{ path: "/wt/merged one", head: "b2", branch: "feat/one", locked: false, prunable: false }`; `[3].branch === null`; `[4].locked === true`; `[5].prunable === true`.
- `note names merged worktrees and skips the primary, open and locked ones` — note matches `/^vibe-workflow: merged worktrees still on disk: /`, includes `/wt/merged one (feat/one)`, `/wt/detached (detached)`, `/wt/moved (feat/moved, needs repair)`, ends with `Sweep them per the worktree cleanup steps in vibe-pull-requests.`; does not include `/repo (`, `/wt/open`, `/wt/locked`.
- `the first note explains the new behavior once` — first non-null note includes `Cleanup now also removes older merged worktrees and tool caches; other files are asked about before the merge.`; second call does not. A call that returns `null` does not consume the explanation.
- `busy and excluded worktrees are left out` — `busy: async () => new Set(["/wt/merged one/sub"])` drops `/wt/merged one`; `note({ exclude: "/wt/detached/src" })` drops `/wt/detached`; with `busy` returning `null`, `exclude` still drops its worktree.
- `more than ten merged worktrees list ten then the rest as a count` — 13 merged branch worktrees → exactly 10 paths and `and 3 more`.
- `silent on failure, timeout, missing origin HEAD or nothing merged` — `note()` returns `null` when: `run` throws; `run` never resolves (deadline 20 ms in the test, and the test finishes in under 200 ms); `for-each-ref` exits 128; `primary()` returns `null`; only the primary is listed.
- `the two list reads run in parallel` — a `run` that records start order and resolves both after a shared gate: both have started before either resolves.
- `busyDirectories keeps running sessions' directories` — stub `ctx.session.list` returning `{ data: [{ time: { busy: started + 10 }, location: { directory: "/wt/a" } }, { time: { busy: started + 10, idle: started + 20 }, location: { directory: "/wt/b" } }] }` → `Set(["/wt/a"])`; a throwing `list` → `null`; items without `location` → `null`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/plugin/merged-worktrees.test.js`
Expected: FAIL, cannot find module `src/merged-worktrees.js`.

- [ ] **Step 3: Implement `src/merged-worktrees.js` and export `isRunning` from `src/session-titles.js`**

Reads, from `await primary()`: in parallel `run(primary, ["worktree", "list", "--porcelain"])` and `run(primary, ["for-each-ref", "--merged", "refs/remotes/origin/HEAD", "--format=%(refname:short)", "refs/heads"])`; then in parallel one `run(primary, ["merge-base", "--is-ancestor", head, "refs/remotes/origin/HEAD"])` per detached entry (exit 0 merged, 1 not, anything else is a failure). Everything races one `deadline` timer. The primary is the first porcelain entry. Containment is "equal to the worktree path or starts with path + `/`". `defaultRun` uses `execFile` with `GIT_*` variables stripped and `LC_ALL=C`, like `defaultCheckout` in `src/pr-advice.js`, and a `timeout` equal to `deadline`. Note shape per the spec: names joined with `, `, then `. Sweep them per the worktree cleanup steps in vibe-pull-requests.`; the explanation sentence is appended after it on the first non-null note per `createSweep` instance.

- [ ] **Step 4: Register the file** in `package.json` `files`, `scripts/manifest.py` and the expected set in `tests/tooling/test_package.py`, after `src/skill-advice.js`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test tests/plugin/merged-worktrees.test.js tests/plugin/session-titles.test.js && python3 -B -m unittest tests.tooling.test_package && python3 -B scripts/validate.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/merged-worktrees.js src/session-titles.js tests/plugin/merged-worktrees.test.js package.json scripts/manifest.py tests/tooling/test_package.py
git commit -m "feat: find merged worktrees for the cleanup note"
```

### Task 2: The pull request notes name merged worktrees

**Files:**
- Modify: `src/pr-advice.js:167-243` (`NOTES`, `createAdvisor`)
- Test: `tests/plugin/pr-advice.test.js`

**Interfaces:**
- Consumes: `createSweep(...).note({ exclude })` from Task 1.
- Produces: `createAdvisor({ settings, primary, checkout, sweep = null })` — `sweep` is `{ note({ exclude }) }` or `null`.

- [ ] **Step 1: Write the failing tests** in `tests/plugin/pr-advice.test.js`, with a fake `sweep` that records calls and returns `"vibe-workflow: merged worktrees still on disk: /wt/x (feat/x). Sweep them per the worktree cleanup steps in vibe-pull-requests."`:
- `the sweep list follows the ready and merge notes only when cleanup is on` — with `{ state: "on", agent_merge: true, worktree_cleanup: true }`: `gh pr ready 3`, `gh pr edit 3 --title "[READY] a"` and `gh pr merge 3` each end with the sweep line on its own line; `gh pr merge 3 --auto`, `gh pr ready --undo`, `gh pr create --draft`, `git push` never call `sweep.note`; with `worktree_cleanup` `false`, `null` or `undefined`, `sweep.note` is never called.
- `the sweep excludes the command's own directory` — `advise("gh pr merge 3", "ses_a", { directory: "/wt/x/src" })` calls `sweep.note` with `{ exclude: "/wt/x/src" }`.
- `a null sweep note or a missing sweep leaves the existing notes unchanged` — the merge note equals today's text when `sweep.note` returns `null`, throws, or `sweep` is `null`.
- `case 1 reminds about leftover files only when cleanup is on` — `gh pr create --draft --title "[BLOCKED] a"` with cleanup on includes `Before marking it [READY], check the worktree's leftover files per the worktree cleanup steps in vibe-pull-requests; delete nothing before the merge.`; with cleanup off it does not; `sweep.note` is not called.
- The existing case 4 tests still pass unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/plugin/pr-advice.test.js`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** — in `advise`, after the action loop, when `cleanup` is true and any action was `pr_ready` (not undo), a `[READY]` retitle or a non-`auto` `pr_merge`, append `await sweep?.note({ exclude: event.directory ?? null })` inside its own `try`, once per call. Add the case 1 sentence to the `pr_create` branch when `cleanup` is true.

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/plugin/pr-advice.test.js tests/plugin/pr-hook.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pr-advice.js tests/plugin/pr-advice.test.js
git commit -m "feat: name merged worktrees after ready and merge"
```

### Task 3: The worktree skill note names merged worktrees

**Files:**
- Modify: `src/skill-advice.js:29-46`
- Test: `tests/plugin/skill-advice.test.js`

**Interfaces:**
- Consumes: `createSweep(...).note({ exclude })` from Task 1.
- Produces: `createSkillAdvisor({ settings, onboarded, sweep = null })`.

- [ ] **Step 1: Write the failing tests** in `tests/plugin/skill-advice.test.js`:
- `the worktree note adds the sweep list when cleanup is on` — with `worktree_cleanup: true` and a fake sweep, `skillNote("using-git-worktrees")` equals `WORKTREE + "\n" + <sweep line>`, and `sweep.note` was called with `{ exclude: null }`; with `worktree_cleanup` `false` or `null`, the note equals `WORKTREE` and `sweep.note` is not called; a sweep returning `null` or throwing yields `WORKTREE`.
- `other skills never read the sweep` — `finishing-a-development-branch`, `writing-plans`, `brainstorming` never call `sweep.note`.
- Existing tests pass unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/plugin/skill-advice.test.js`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** — after `entry.note(state)` for `using-git-worktrees` only, when `state.worktree_cleanup === true`, append the sweep note on a new line, inside its own `try`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/plugin/skill-advice.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill-advice.js tests/plugin/skill-advice.test.js
git commit -m "feat: name merged worktrees when a worktree is created"
```

### Task 4: Wire the sweep into the plugin

**Files:**
- Modify: `src/advice.js:25-27`, `index.js:565-566`
- Test: `tests/plugin/plugin.test.js`

**Interfaces:**
- Consumes: `createSweep`, `busyDirectories` (Task 1); `createAdvisor({ ..., sweep })` (Task 2); `createSkillAdvisor({ ..., sweep })` (Task 3); `serverStart` from `src/session-titles.js`.
- Produces: `registerAdvice(ctx, { settings, onboarded, primary, checkout, sweep })`; `index.js` passes `sweep: createSweep({ primary, busy: busyDirectories(ctx, serverStart()) })`, using the same `primary` function it already passes.

- [ ] **Step 1: Write the failing test** in `tests/plugin/plugin.test.js`: `the merge note carries the sweep list through the registered hook` — set up the plugin as the file's existing hook tests do, with `git_workflow` on and `worktree_cleanup: true` and a temporary repository holding one merged linked worktree (reuse the fixture style of `tests/skills/worktree-cleanup.test.js`, with `refs/remotes/origin/HEAD` set by `git remote set-head origin main`); run the `execute.after` handler on a completed `gh pr merge 3`; the result content includes `merged worktrees still on disk:` and the worktree's path.

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/plugin/plugin.test.js`
Expected: FAIL, the line is missing.

- [ ] **Step 3: Implement the wiring** described in Interfaces.

- [ ] **Step 4: Run the whole Node suite**

Run: `node --test "tests/**/*.test.js"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/advice.js index.js tests/plugin/plugin.test.js
git commit -m "feat: wire the merged-worktree sweep into the advice hook"
```

### Task 5: The cleanup rules: sort, timing, sweep, repair, archive

**Files:**
- Modify: `skills/vibe-pull-requests/references/worktree-cleanup.md`, `skills/vibe-pull-requests/references/procedure.md` ("Readiness", "Merging", "The primary checkout")
- Test: `tests/skills/pull-request-skill.test.js`, `tests/skills/worktree-cleanup.test.js`

**Interfaces:**
- Consumes: the note wording from Tasks 1–2 (the reference is what "the worktree cleanup steps in vibe-pull-requests" points to).

- [ ] **Step 1: Write the failing text test** in `tests/skills/pull-request-skill.test.js`: `the cleanup reference sorts leftovers, waits for the merge and sweeps older worktrees` — `worktree-cleanup.md` includes each of: every name in the rebuildable list; `!! build/`; `Nothing is deleted, moved or archived before the merge is confirmed`; `Commit to this PR`; `Archive`; `Discard`; `Keep the worktree`; `.vibe-workflow/.archives/<branch>/`; `.vibe-workflow/.archives/.gitignore`; `Cleanup verified`; `Cleanup blocked`; `## Sweeping older merged worktrees`; `gh pr list --head <branch> --state merged`; `git -C <worktree> switch --detach`; `worktree repair`; `prunable`. The sweep section's text includes `never` within the line that mentions `force`. `procedure.md` includes `.vibe-workflow/.archives/` in "The primary checkout" and `leftover files` in "Readiness". The existing `-D` line check still passes.

- [ ] **Step 2: Write the failing git fixture tests** in `tests/skills/worktree-cleanup.test.js`, reusing `repository(t)`:
- `listed caches can be deleted after the merge, then the worktree is removable` — ignore `node_modules/`, `__pycache__/`, `.DS_Store`; create them; `status --porcelain --ignored` lists only `!!` entries ending in listed names; after `rm` of each, status is empty and `git worktree remove` (no force) succeeds.
- `unlisted ignored folders, .env and untracked files stay real work` — ignore `/build/` and `.env`; create `build/.env` and `.env` and an untracked `notes.md`; status shows `!! build/` (collapsed, `build/.env` not visible), `!! .env`, `?? notes.md`; none of these lines ends in a listed name.
- `worktree repair fixes a worktree after the repository moved` — move the primary folder with `fs.rename`; `git -C <worktree> status` fails; `git -C <moved primary> worktree repair <worktree>` succeeds; `git -C <worktree> status` then succeeds.
- `the archive folder ignores itself` — create `<primary>/.vibe-workflow/.archives/.gitignore` containing `*` and a file `.vibe-workflow/.archives/feat/x/notes.md`; `git -C <primary> status --porcelain` is empty.

- [ ] **Step 3: Run tests to verify the text test fails**

Run: `node --test tests/skills/pull-request-skill.test.js tests/skills/worktree-cleanup.test.js`
Expected: the text test FAILS; the git fixture tests already PASS (they pin git behavior the reference relies on).

- [ ] **Step 4: Rewrite the reference and procedure** to the spec's sections 1–4 and 7. Keep every existing check, step, table and phrase the current tests assert. Changes:
  - Check 4 becomes the sort (spec section 1), with actions only after checks 1–3 and 5 pass.
  - New "Before `[READY]`" section (spec section 2): read-only sort, the question and its per-group choices, Commit before `[READY]`, Archive and Discard after the merge, answer lives in the session.
  - Report form (spec section 3): exactly one of **Cleanup verified** or **Cleanup blocked**, never ending on a question.
  - New `## Sweeping older merged worktrees` (spec section 4) with the repair step and the residual-risk line.
  - Archive (spec section 7): path, nesting, `-<YYYYMMDD-HHMMSS>` suffix on collision, self-ignoring `.gitignore` written before moving anything and rewritten if missing.
  - `procedure.md`: "Readiness" adds that, when cleanup is on, the leftover-file check is settled before `[READY]`; "The primary checkout" adds `.vibe-workflow/.archives/` as the second exception next to `.vibe-workflow/settings.json`, writing new files only.

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test tests/skills/pull-request-skill.test.js tests/skills/worktree-cleanup.test.js && python3 -B scripts/validate.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/vibe-pull-requests/references tests/skills/pull-request-skill.test.js tests/skills/worktree-cleanup.test.js
git commit -m "docs: sort leftovers, wait for the merge and sweep merged worktrees"
```

### Task 6: Onboarding promise, product and development notes

**Files:**
- Modify: `skills/vibe-onboard-project/references/procedure.md` (question 3 context), `tests/plugin/onboarding.test.js:344`, `PRODUCT.md:61-65`, `docs/development.md` (new short section after the note observations near line 140)

- [ ] **Step 1: Update the test first** — in `tests/plugin/onboarding.test.js`, replace the phrase `never removes unmerged work, uncommitted work or ignored files` with `It also removes older merged worktrees and tool caches such as` and add `asks before the merge about any other files left in the folder` for question index 2.

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/plugin/onboarding.test.js`
Expected: FAIL on the new phrases.

- [ ] **Step 3: Update the texts**
  - Onboarding question 3 context: replace "It never removes unmerged work, uncommitted work or ignored files; it stops and tells you." with "It also removes older merged worktrees and tool caches such as `node_modules`. It never removes unmerged work, and asks before the merge about any other files left in the folder." Keep the shape-test limits.
  - `PRODUCT.md` "Post-merge cleanup": add that it also sweeps older merged worktrees named by the plugin, deletes only tool caches on its own, asks about other files before `[READY]`, and archives into `.vibe-workflow/.archives/`.
  - `docs/development.md`: record the 2026-11-27 timing (18.6 ms sequential on 7 worktrees and 18 branches; reads only at the three moments; 500 ms deadline) and mark unobserved live: the sweep line in a real session, and whether `ctx.session.list` without `parentID` returns `location.directory`.

- [ ] **Step 4: Run the full checks**

Run: `python3 -B scripts/validate.py && python3 -B -m unittest discover -s tests -t . && node --test "tests/**/*.test.js" && python3 -B scripts/package.py --output "$TMPDIR/vibe-dist"`
Expected: all pass; the package includes `src/merged-worktrees.js`.

- [ ] **Step 5: Commit**

```bash
git add skills/vibe-onboard-project/references/procedure.md tests/plugin/onboarding.test.js PRODUCT.md docs/development.md
git commit -m "docs: state the broader cleanup in onboarding and product notes"
```
