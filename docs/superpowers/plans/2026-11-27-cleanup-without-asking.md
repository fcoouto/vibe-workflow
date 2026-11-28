# Cleanup Without Asking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents clean up merged worktrees (their own and older ones) without the user asking, deleting only tool caches on their own, never before the merge, and asking about real work before `[READY]`.

**Architecture:** One new plugin module, `src/merged-worktrees.js`, finds merged worktrees with two or three local read-only git calls under one cancelling 500 ms deadline and formats one note. The existing `gh pr ready` / `[READY]` / `gh pr merge` notes (`src/pr-advice.js`) and the `using-git-worktrees` skill note (`src/skill-advice.js`) append it. Everything that deletes stays agent guidance in `skills/vibe-pull-requests/references/worktree-cleanup.md`, which lands before the hook is wired.

**Tech Stack:** Node ES modules (`node:child_process`, `node:test`), Python 3.11+ standard library for packaging checks. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-27-cleanup-without-asking-design.md`

## Global Constraints

- Plugin: read-only git, no `gh`, no network, no write command, no cache, no background work, no new dependencies.
- Reads run only at three moments: after a successful `gh pr ready` (not `--undo`) or retitle to `[READY]`, after a successful `gh pr merge` without `--auto`, and when `using-git-worktrees` loads. Only when `state` is `"on"` and `worktree_cleanup === true`.
- One 500 ms deadline per note, started before the first read, shared by every read and the session list, and cancelling: on expiry every running git child is killed through an `AbortSignal` and no new one starts. On timeout or any failure the note is omitted and existing text is unchanged.
- Rebuildable list (exact): `node_modules/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.DS_Store`. An entry qualifies only when its status is `!!` and the last path component equals a listed name exactly (`*.pyc`: ends in `.pyc`).
- Nothing is deleted, moved or archived before the merge is confirmed.
- Archive folder: `<primary checkout>/.vibe-workflow/archives/<YYYY-MM-DD>_<branch, "/" as "-">/`, self-ignoring via `.vibe-workflow/archives/.gitignore` containing exactly `*`. Written only by the archive script, never by hand.
- Depends on PR #408 (`skills/vibe-security-review/scripts/store_report.py`): before Task 1, rebase this branch onto `main` after #408 merges, then run the full suite once and note what already fails.
- Skill scripts stay Python 3.11 standard library, run as `python3 -I -B '<skill folder>/scripts/<name>.py'`.
- Never `--force`, `-D`, or `worktree_remove` with `force`. Archive never overwrites and never follows a symbolic link.
- The upgrade explanation is sent once per plugin process (module-level flag), on the first non-null sweep note.
- Settings unchanged; `worktree_cleanup` stays the only switch.
- New shipped files go in `package.json` `files`, `scripts/manifest.py` and `tests/tooling/test_package.py` in the same change. README changes only through `impeccable` (none planned).
- This plan changes injected notes, so `python3 -B scripts/live_check.py --injection` runs before the branch is called done (AGENTS.md).
- No real cleanup of this repository's worktrees during development.

## Review Focus

1. `refs/remotes/origin/HEAD` missing (a clone without it): the reads fail and the note is silent; the existing merge note is unchanged. Test in Task 1.
2. A worktree path with spaces: parsed whole and named whole in the note. Test in Task 1.
3. A prunable worktree (folder moved or gone): still named, flagged `needs repair`, never silently dropped. Test in Task 1.
4. A busy chat beyond the first page of sessions: the list is followed up to 4 pages; if more remain, the note is suppressed. Test in Task 1.
5. The calling session sits in a merged worktree, its shell `workdir` points elsewhere, and the host's session list gives no directories: the worktree is still excluded through the session's own folder. Test in Task 6.

---

### Task 1: The merged-worktree finder and its note

**Files:**
- Create: `src/merged-worktrees.js`
- Modify: `src/session-titles.js:135` (use a shared `isRunning`)
- Modify: `package.json` (`files`), `scripts/manifest.py:9`, `tests/tooling/test_package.py:103`
- Test: `tests/plugin/merged-worktrees.test.js`

**Interfaces:**
- Produces:
  - `export function isRunning(time, started): boolean` in `src/session-titles.js`, the predicate of today's inner `running` (`finite(time?.busy) && time.busy >= started && !(finite(time.idle) && time.idle >= time.busy)`); the inner `running` becomes `(time) => isRunning(time, started)`.
  - `export function parseWorktrees(porcelain: string): Array<{ path: string, head: string|null, branch: string|null, locked: boolean, prunable: boolean }>` — `branch` is the short name (`refs/heads/` stripped) or `null` when detached.
  - `export function createSweep({ primary, run = defaultRun, busy = async () => null, deadline = 500, process = PROCESS }): { note({ exclude }: { exclude?: Array<string|null|undefined> }): Promise<string|null> }`
    - `primary: async () => string|null` (the primary checkout path; the existing `primary` helper in `index.js`).
    - `run: async (cwd: string, args: string[], signal: AbortSignal) => { code: number, stdout: string }` — never throws for a nonzero exit; throws when git cannot start or the signal aborted it.
    - `busy: async (signal: AbortSignal) => { complete: boolean, dirs: Set<string> } | null` — `null` means the host gives no session directories (known risk: only `exclude` protects); `complete: false` means pages remained and the note is suppressed.
    - `process: { explained: boolean }` — defaults to the module-level `PROCESS` object, so the explanation is once per plugin process across instances; tests pass their own object.
  - `export function busyDirectories(ctx, started): (signal: AbortSignal) => Promise<{ complete: boolean, dirs: Set<string> } | null>` — `ctx.session.list({ limit: 50, order: "desc" })`, following `cursor.next` with `ctx.session.list({ cursor, limit: 50 })` for at most 4 pages and stopping when `signal` aborts; keeps items where `isRunning(item.time, started)`; collects their `location.directory`. Returns `null` when the first call fails or a running item has no string `location.directory`; `complete: false` when a fifth page exists or the signal aborted.

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
const SWEEP = "Sweep them per the worktree cleanup steps in vibe-pull-requests."
const EXPLAIN = "Tell your human partner once in your report: cleanup now also removes older merged worktrees and " +
  "tool caches; other files are asked about before the merge."
```

Tests and assertions:
- `parseWorktrees reads paths with spaces, branches, detached, locked and prunable` — `parseWorktrees(PORCELAIN)[1]` deep-equals `{ path: "/wt/merged one", head: "b2", branch: "feat/one", locked: false, prunable: false }`; `[3].branch === null`; `[4].locked === true`; `[5].prunable === true`.
- `note names merged worktrees and skips the primary, open and locked ones` — note starts with `vibe-workflow: merged worktrees still on disk: `, includes `/wt/merged one (feat/one)`, `/wt/detached (detached)`, `/wt/moved (feat/moved, needs repair)` and `SWEEP`; does not include `/repo (`, `/wt/open`, `/wt/locked`.
- `the explanation is sent once per process object` — with a shared `{ explained: false }` passed to two `createSweep` instances: the first non-null note ends with `SWEEP + " " + EXPLAIN`; the next note from either instance ends with `SWEEP`. A `null` note does not consume it.
- `busy and excluded worktrees are left out` — `busy` returning `{ complete: true, dirs: new Set(["/wt/merged one/sub"]) }` drops `/wt/merged one`; `note({ exclude: [null, "/wt/detached/src"] })` drops `/wt/detached`; with `busy` returning `null`, `exclude` still drops its worktree; `/wt/merged oneX` is not contained by `/wt/merged one`.
- `an incomplete session list suppresses the note` — `busy` returning `{ complete: false, dirs: new Set() }` → `null`.
- `more than ten merged worktrees list ten then the rest as a count` — 13 merged branch worktrees → exactly 10 paths and `and 3 more`.
- `silent on failure, missing origin HEAD or nothing merged` — `null` when: `run` throws; `for-each-ref` exits 128; `primary()` returns `null`; only the primary is listed.
- `one cancelling deadline covers every read` — deadline 20 ms; the list reads resolve at 15 ms, the detached check hangs until its signal aborts: `note()` returns `null` by 60 ms; every `run` call received the same signal, it is aborted, and no `run` call started after it aborted. A second case where the list reads hang returns `null` and never starts the detached check.
- `the two list reads run in parallel` — a `run` that records starts and resolves both after a shared gate: both started before either resolved.
- `busyDirectories follows pages and reports completeness` — stub `ctx.session.list`: page 1 has a finished item, page 2 has `{ time: { busy: started + 10 }, location: { directory: "/wt/a" } }` → `{ complete: true, dirs: Set(["/wt/a"]) }`; five full pages → `complete: false`; a throwing first call → `null`; a running item without `location` → `null`; an aborted signal before page 2 → `complete: false`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/plugin/merged-worktrees.test.js`
Expected: FAIL, cannot find module `src/merged-worktrees.js`.

- [ ] **Step 3: Implement `src/merged-worktrees.js` and export `isRunning` from `src/session-titles.js`**

Per `note()` call: one `AbortController`, a timer that aborts it at `deadline`, cleared on finish. Reads from `await primary()`, in parallel: `run(primary, ["worktree", "list", "--porcelain"], signal)`, `run(primary, ["for-each-ref", "--merged", "refs/remotes/origin/HEAD", "--format=%(refname:short)", "refs/heads"], signal)` and `busy(signal)`; then, only if not aborted, in parallel one `run(primary, ["merge-base", "--is-ancestor", head, "refs/remotes/origin/HEAD"], signal)` per detached entry (exit 0 merged, 1 not, anything else is a failure). Any failure or abort returns `null`. The primary is the first porcelain entry. Containment is "equal to the worktree path or starts with path + `/`". `defaultRun` uses `execFile` with `{ signal }`, `GIT_*` variables stripped and `LC_ALL=C`, like `defaultCheckout` in `src/pr-advice.js`. Note: `vibe-workflow: merged worktrees still on disk: ` + names joined with `, ` + `. ` + `SWEEP`, then ` ` + `EXPLAIN` when `process.explained` is false (then set it true).

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
- Consumes: `{ note({ exclude }) }` shaped like Task 1's `createSweep` result.
- Produces: `createAdvisor({ settings, primary, checkout, sweep = null })`. It calls `sweep.note({ exclude: [event.directory] })`; the session's own folder is added by the wiring in Task 6.

- [ ] **Step 1: Write the failing tests** in `tests/plugin/pr-advice.test.js`, with a fake `sweep` that records calls and returns `"vibe-workflow: merged worktrees still on disk: /wt/x (feat/x). Sweep them per the worktree cleanup steps in vibe-pull-requests."`:
- `the sweep list follows the ready and merge notes only when cleanup is on` — with `{ state: "on", agent_merge: true, worktree_cleanup: true }`: `gh pr ready 3`, `gh pr edit 3 --title "[READY] a"` and `gh pr merge 3` each end with `\n` + the sweep line; `gh pr merge 3 --auto`, `gh pr ready --undo`, `gh pr create --draft`, `git push` never call `sweep.note`; with `worktree_cleanup` `false`, `null` or `undefined`, `sweep.note` is never called; `gh pr ready 3 && gh pr merge 3` calls it once.
- `the sweep excludes the command's directory` — `advise("gh pr merge 3", "ses_a", { directory: "/wt/x/src" })` calls `sweep.note` with `{ exclude: ["/wt/x/src"] }`.
- `a null sweep note or a missing sweep leaves the existing notes unchanged` — the merge note equals today's text when `sweep.note` returns `null`, throws, or `sweep` is `null`.
- `case 1 reminds about leftover files only when cleanup is on` — `gh pr create --draft --title "[BLOCKED] a"` with cleanup on includes `Before marking it [READY], check the worktree's leftover files per the worktree cleanup steps in vibe-pull-requests; delete nothing before the merge.`; with cleanup off it does not; `sweep.note` is not called.
- The existing case 4 tests still pass unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/plugin/pr-advice.test.js`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** — in `advise`, after the action loop, when `cleanup` is true and any action was `pr_ready` (not undo), a `[READY]` retitle or a non-`auto` `pr_merge`, append `await sweep?.note({ exclude: [event.directory] })` inside its own `try`, once per call. Add the case 1 sentence to the `pr_create` branch when `cleanup` is true.

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
- Consumes: `{ note({ exclude }) }` as in Task 2.
- Produces: `createSkillAdvisor({ settings, onboarded, sweep = null })`. It calls `sweep.note({ exclude: [] })`; the session's own folder is added by the wiring in Task 6.

- [ ] **Step 1: Write the failing tests** in `tests/plugin/skill-advice.test.js`:
- `the worktree note adds the sweep list when cleanup is on` — with `worktree_cleanup: true` and a fake sweep, `skillNote("using-git-worktrees")` equals `WORKTREE + "\n" + <sweep line>`, and `sweep.note` was called with `{ exclude: [] }`; with `worktree_cleanup` `false` or `null`, the note equals `WORKTREE` and `sweep.note` is not called; a sweep returning `null` or throwing yields `WORKTREE`.
- `other skills never read the sweep` — `finishing-a-development-branch`, `writing-plans`, `brainstorming` never call `sweep.note`.
- Existing tests pass unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/plugin/skill-advice.test.js`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** — for `using-git-worktrees` only, when `state.worktree_cleanup === true`, append the sweep note after `entry.note(state)` on a new line, inside its own `try`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/plugin/skill-advice.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skill-advice.js tests/plugin/skill-advice.test.js
git commit -m "feat: name merged worktrees when a worktree is created"
```

### Task 4: Shared private-folder module and the archive script

**Files:**
- Create: `runtime/private_folder.py` (moved out of `store_report.py`)
- Modify: `skills/vibe-security-review/scripts/store_report.py` (import the shared checks; behavior unchanged)
- Create: `skills/vibe-pull-requests/scripts/archive.py`
- Modify: `scripts/manifest.py`, `tests/tooling/test_package.py` (`package.json` `files` already covers `runtime` and `skills/vibe-pull-requests`)
- Test: `tests/tooling/test_archive.py`; `tests/tooling/test_store_report.py` must pass unchanged

**Interfaces:**
- Produces, in `runtime/private_folder.py` (moved, same bodies): `Refused(Exception)`, `git(directory, *args)`, `main_checkout(cwd) -> str`, and
  `open_private_folder(main: str, name: str) -> tuple[int, list[int]]` — runs today's `store()` lines 170–191 for `.vibe-workflow/<name>` (link and directory checks, `ls-files` refusal, `_trusted` on the checkout and `.vibe-workflow`, `_open_folder`, owner check, `fchmod 0700`, `_ensure_ignore`); returns the folder descriptor and every descriptor to close; the caller closes them. `_ensure_ignore`'s refusal text names the folder generically (`the private folder's .gitignore ...`); update the one matching assertion in `test_store_report.py` only if it quotes that text, and say so in the commit.
  - Both scripts reach it by inserting the package root, `os.path.realpath(__file__)` three levels up, at `sys.path[0]` before `from runtime.private_folder import ...`. The root is checked to contain `runtime/private_folder.py`; otherwise `refused: the plugin's files are incomplete`.
- Produces the CLI: `python3 -I -B '<skill folder>/scripts/archive.py' --worktree '<path>' --branch '<branch>' --path '<relative path>' [--path ...]`
  - Exit 0: prints one absolute destination per archived path. Exit 2: prints `archived: <dest>` for each path already moved, then `refused: <reason>`; nothing further moves.
  - Destination: `<main>/.vibe-workflow/archives/<YYYY-MM-DD>_<branch, "/" as "-">[-N]/<relative path>`; the run's folder is created with `mkdir` that fails if it exists, then retried with the next suffix (up to `-99`).
  - Refuses: `--worktree` not a linked worktree of the repository whose main checkout `main_checkout` finds (compare `git rev-parse --path-format=absolute --git-common-dir`); a branch name that `git check-ref-format --branch` rejects; an absolute path, a `..` component, or any parent component that is a link (each opened with `O_NOFOLLOW` from the worktree's descriptor); a path that `git -C <worktree> ls-files` lists; a destination that `git -C <main> check-ignore -q` does not ignore; an existing destination entry; a rename that fails with `EXDEV` (never falls back to copying).
  - Moves with `os.rename(name, name, src_dir_fd=..., dst_dir_fd=...)` only, so links inside a moved folder move as links.

- [ ] **Step 1: Write the failing tests** in `tests/tooling/test_archive.py`, in the fixture style of `tests/tooling/test_store_report.py` (temporary main checkout plus a linked worktree, global git config ignored, umask-independent):
- `test_moves_ignored_and_untracked_paths_keeping_layout` — `.env` (ignored) and `reports/a.md` (untracked) land at `archives/feat/x/.env` and `archives/feat/x/reports/a.md`; sources are gone; exit 0 prints both destinations; `git -C <main> status --porcelain` is empty.
- `test_second_run_gets_a_suffix_and_never_overwrites` — a second archive of the same branch goes to `<YYYY-MM-DD>_feat-x-2/`; the first run's files are unchanged.
- `test_refuses_tracked_absolute_dotdot_and_linked_parent` — each case exits 2 with `refused:` and moves nothing.
- `test_refuses_a_linked_archives_folder_or_gitignore` — `archives` as a link, and `archives/.gitignore` as a link: refused, link targets untouched.
- `test_rewrites_a_differing_gitignore` — `archives/.gitignore` holding `*.log` becomes exactly `*\n` before any move.
- `test_cross_device_is_refused_and_source_kept` — patch `os.rename` to raise `OSError(errno.EXDEV, ...)`: exit 2, source intact, no copy at the destination.
- `test_partial_progress_is_reported` — first path moves, second is tracked: output has `archived: <first dest>` then `refused:`; the first stays archived, the second stays in place.
- `test_refuses_a_worktree_of_another_repository` — refused, nothing created.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -B -m unittest tests.tooling.test_archive`
Expected: FAIL, the script does not exist.

- [ ] **Step 3: Move the shared checks** into `runtime/private_folder.py` and make `store_report.py` import them, following the `safe-refactor` skill. Run `python3 -B -m unittest tests.tooling.test_store_report` and expect PASS before going on.

- [ ] **Step 4: Implement `archive.py`** per Interfaces.

- [ ] **Step 5: Register** `runtime/private_folder.py` and `skills/vibe-pull-requests/scripts/archive.py` in `scripts/manifest.py` and the expected set in `tests/tooling/test_package.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -B -m unittest tests.tooling.test_archive tests.tooling.test_store_report tests.tooling.test_package && python3 -B scripts/validate.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/private_folder.py skills/vibe-security-review/scripts/store_report.py skills/vibe-pull-requests/scripts/archive.py tests/tooling/test_archive.py tests/tooling/test_package.py scripts/manifest.py
git commit -m "feat: archive leftover files through the shared private-folder checks"
```

### Task 5: The cleanup rules: sort, timing, sweep, repair, archive

Lands before Task 6, so the shipped reference already allows the sweep when the hook starts naming worktrees.

**Files:**
- Modify: `skills/vibe-pull-requests/references/worktree-cleanup.md`, `skills/vibe-pull-requests/references/procedure.md` ("Readiness", "The primary checkout")
- Test: `tests/skills/pull-request-skill.test.js`, `tests/skills/worktree-cleanup.test.js`

**Interfaces:**
- Consumes: the note wording from Tasks 1–2 (the reference is what "the worktree cleanup steps in vibe-pull-requests" points to); the archive command and its outputs from Task 4.

- [ ] **Step 1: Write the failing text test** in `tests/skills/pull-request-skill.test.js`: `the cleanup reference sorts leftovers, waits for the merge and sweeps older worktrees` — `worktree-cleanup.md` includes each of: every name in the rebuildable list; `!! build/`; `last path component`; `Nothing is deleted, moved or archived before the merge is confirmed`; `Commit to this PR`; `Archive`; `Discard`; `Keep the worktree`; `list them again`; `docs/vibe-workflow/council-sessions/`; `result.md`; `(Recommended)`; `.vibe-workflow/archives/<branch>/`; `scripts/archive.py`; `python3 -I -B`; `never by hand`; `refused:`; `Cleanup verified`; `Cleanup blocked`; `## Sweeping older merged worktrees`; `gh pr list --head <branch> --state merged`; `git -C <worktree> switch --detach`; `worktree repair`; `prunable`. Every line that mentions `force` also matches `/never/i`. `procedure.md` includes `.vibe-workflow/archives/` and `leftover files`. The existing `-D` line check still passes.

- [ ] **Step 2: Write the git fixture tests** in `tests/skills/worktree-cleanup.test.js`, reusing `repository(t)`; they pin the git behavior the rules rely on:
- `listed caches can be deleted after the merge, then the worktree is removable` — ignore `node_modules/`, `__pycache__/`, `.DS_Store`; create them; every `status --porcelain --ignored` line starts with `!! ` and its last path component (trailing `/` removed) is a listed name; after `rm` of each, status is empty and `git worktree remove` (no force) succeeds.
- `look-alike and unignored caches are not rebuildable` — without an ignore rule, `node_modules/` shows as `?? node_modules/`; with `mynode_modules/` ignored, it shows as `!! mynode_modules/`, whose last component is not a listed name.
- `unlisted ignored folders, .env and untracked files stay real work` — ignore `/build/` and `.env`; create `build/.env`, `.env` and an untracked `notes.md`; status shows `!! build/` (collapsed, no `build/.env` line), `!! .env`, `?? notes.md`.
- `worktree repair fixes a worktree after the repository moved` — rename the primary folder; `git -C <worktree> status` fails; `git -C <moved primary> worktree repair <worktree>` succeeds; `git -C <worktree> status` then succeeds.

- [ ] **Step 3: Run tests to verify the text test fails**

Run: `node --test tests/skills/pull-request-skill.test.js tests/skills/worktree-cleanup.test.js`
Expected: the text test FAILS; the git fixture tests PASS.

- [ ] **Step 4: Rewrite the reference and procedure** to the spec's sections 1–4 and 7, keeping every existing check, step, table and phrase the current tests assert. Changes:
  - Check 4 becomes the sort (spec section 1), with the exact-match rule from Global Constraints, and actions only after checks 1–3 and 5 pass.
  - New "Before `[READY]`" section (spec section 2): read-only sort; the question and its per-group choices; record the exact paths and, for a folder, its file list shown to the user; the recommended choices for council records from spec section 2 (session with `result.md`: Commit; without it, or the folder ignored: Archive); Commit before `[READY]`; Archive and Discard after the merge, and only after you list them again and they match what the user approved — anything new or changed stops that group and is reported; the answer lives in the session.
  - Report form (spec section 3): exactly one of **Cleanup verified** or **Cleanup blocked**, never ending on a question.
  - New `## Sweeping older merged worktrees` (spec section 4) with the detach step, the repair step for `prunable` or failing worktrees, the rerun of the sort right before removal, and the residual-risk line.
  - Archive (spec section 7): never by hand; run Task 4's command with every argument shell-quoted, one `--path` per approved path, after the merge and after listing them again. On exit 0, report the printed destinations. On `refused:`, report the reason and the `archived:` lines, archive nothing more by other means, and mark cleanup of that worktree blocked.
  - `procedure.md`: "Readiness" adds that, when cleanup is on, the leftover files question is settled before `[READY]`; "The primary checkout" adds `.vibe-workflow/archives/` as an exception next to `.vibe-workflow/settings.json` and the security reports folder, written only by the archive script.

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test tests/skills/pull-request-skill.test.js tests/skills/worktree-cleanup.test.js && python3 -B scripts/validate.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/vibe-pull-requests/references tests/skills/pull-request-skill.test.js tests/skills/worktree-cleanup.test.js
git commit -m "docs: sort leftovers, wait for the merge and sweep merged worktrees"
```

### Task 6: Wire the sweep into the plugin

**Files:**
- Modify: `src/advice.js:25-27`, `index.js:565-566`
- Test: `tests/plugin/plugin.test.js`

**Interfaces:**
- Consumes: `createSweep`, `busyDirectories` (Task 1); `createAdvisor({ ..., sweep })` (Task 2); `createSkillAdvisor({ ..., sweep })` (Task 3); `serverStart` from `src/session-titles.js`.
- Produces: `registerAdvice(ctx, { settings, onboarded, primary, checkout, sweep })`. `registerAdvice` wraps it as `{ note: ({ exclude = [] } = {}) => sweep.note({ exclude: [ctx.location?.directory, ...exclude] }) }` before passing it to both advisors; the hook copy runs in the session's own folder, so `ctx.location.directory` is that folder (see the comment in `tests/plugin/session-titles.test.js:104`). `index.js` passes `sweep: createSweep({ primary, busy: busyDirectories(ctx, serverStart()) })`, using the `primary` function it already passes.

- [ ] **Step 1: Write the failing tests** in `tests/plugin/plugin.test.js`, setting up the plugin as the file's existing hook tests do, with `git_workflow` on and `worktree_cleanup: true`, and a temporary repository (fixture style of `tests/skills/worktree-cleanup.test.js`, `git remote set-head origin main`) holding two merged linked worktrees `a` and `b`:
- `the merge note carries the sweep list through the registered hook` — `execute.after` on a completed `gh pr merge 3` with `ctx.location.directory` at the primary: the content includes `merged worktrees still on disk:` and both paths.
- `the session's own worktree is excluded even when its workdir points elsewhere` — `ctx.location.directory` is worktree `a`, `ctx.session.list` returns items without `location`, the shell `workdir` is the primary: the note names `b` and not `a`. The same holds for a completed `skill` call loading `using-git-worktrees`.

- [ ] **Step 2: Run tests to verify they fail**

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

### Task 7: Onboarding promise, product notes and the final gate

**Files:**
- Modify: `skills/vibe-onboard-project/references/procedure.md` (question 3 context), `tests/plugin/onboarding.test.js:344`, `PRODUCT.md:61-65`, `docs/development.md` (a short section after the note observations near line 140)

- [ ] **Step 1: Update the test first** — in `tests/plugin/onboarding.test.js`, replace the phrase `never removes unmerged work, uncommitted work or ignored files` with `It also removes older merged worktrees and tool caches such as` and add `asks before the merge about any other files left in the folder`, both for question index 2.

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/plugin/onboarding.test.js`
Expected: FAIL on the new phrases.

- [ ] **Step 3: Update the texts**
  - Onboarding question 3 context: replace "It never removes unmerged work, uncommitted work or ignored files; it stops and tells you." with "It also removes older merged worktrees and tool caches such as `node_modules`. It never removes unmerged work, and asks before the merge about any other files left in the folder." Keep the shape-test limits.
  - `PRODUCT.md` "Post-merge cleanup": add that it also sweeps older merged worktrees named by the plugin, deletes only tool caches on its own, asks about other files before `[READY]`, and archives into `.vibe-workflow/archives/`.
  - `docs/development.md`: record the 2026-11-27 timing (18.6 ms sequential on 7 worktrees and 18 branches; reads only at the three moments; one cancelling 500 ms deadline) and mark unobserved live: the sweep line in a real session, and whether `ctx.session.list` without `parentID` returns `location.directory`.

- [ ] **Step 4: Run the full checks**

Run: `python3 -B scripts/validate.py && python3 -B -m unittest discover -s tests -t . && node --test "tests/**/*.test.js" && python3 -B scripts/package.py --output "$TMPDIR/vibe-dist"`
Expected: all pass; the package includes `src/merged-worktrees.js`.

- [ ] **Step 5: Commit**

```bash
git add skills/vibe-onboard-project/references/procedure.md tests/plugin/onboarding.test.js PRODUCT.md docs/development.md
git commit -m "docs: state the broader cleanup in onboarding and product notes"
```

- [ ] **Step 6: Run the injection check** (required because injected notes changed)

Run: `python3 -B scripts/live_check.py --injection`
Expected: passes. If it fails, stop and tell the user; do not change code to make it pass.

- [ ] **Step 7: Independent behavior and security review** of the whole branch by a fresh reviewer, given the spec, this plan and the diff against `main`. Fix findings, commit, then rerun the affected tests from Step 4 and report their output.
