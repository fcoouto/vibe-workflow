# Title Meter Branch Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the title meter reach cell 5 when merge and cleanup finish in one turn, count same-line `**Files:**` paths, and store diagnostics.

**Architecture:** Plugin-only. `session-facts.js` gains error reporting and `branchOf`; `session-titles.js` records the branch from shell tool events and saves a `diag` block.

**Tech Stack:** Node ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-22-title-meter-branch-record-design.md`

## Global Constraints

- Standard library only; the plugin never writes to git or the title at tool time.
- `diag` holds no timestamps, no stderr and no paths; it joins the save snapshot.
- An unchanged turn writes nothing to storage (existing test `an unchanged turn writes nothing to storage`).
- Reuse `classify` from `pr-advice.js` and the existing `enqueue`, `on()`, `load`, `keep`, `save`.

## Review Focus

- A push to a branch without upstream still records it (Task 3).
- A shell command for another task's directory never records its branch: status runs in `task.top` (Task 3).
- A child session or an unbound session records nothing (Task 3).
- A stored `diag` with extra or non-string fields is dropped, not trusted (Task 2).

---

### Task 1: Same-line `**Files:**` paths

**Files:** Modify `session-facts.js` (`declaredPaths`), `tests/session-facts.test.js` (~line 52)

- [ ] **Step 1:** Add test `declaredPaths also reads paths on the Files line itself`: `"**Files:** Modify `a/b.md` (`FEATURES`), `tests/c.js`\n\nText `x.js`\n"` returns `["a/b.md", "tests/c.js"]`; a mixed block (`**Files:** Modify `a.js`` then `- Test: `t.js``) returns `["a.js", "t.js"]`.
- [ ] **Step 2:** Run `node --test tests/session-facts.test.js`; expect the new test to FAIL.
- [ ] **Step 3:** In `declaredPaths`, parse backticked tokens from the text after `**Files:**` with the same `[./]` filter and `:line` stripping as the following lines.
- [ ] **Step 4:** Run it again; expect PASS. Commit `fix: read plan paths on the Files line itself`.

### Task 2: Per-step errors and the `diag` block

**Files:** Modify `session-facts.js` (`readFacts`), `session-titles.js` (`load`, the turn-end read, `save`), `tests/session-facts.test.js`, `tests/session-titles.test.js`

**Interfaces:**
- Produces: `readFacts(...)` result gains `errors: Array<{ step: "status"|"diff"|"plan"|"pr", code: string }>`; saved record gains `diag: { instance, exists, branch, upstream, head, pr, declared: number, done: number, errors }` (`pr` is the PR state).

- [ ] **Step 1:** Add tests: (a) `readFacts reports which read failed` (use `remembered: "feat/x"` and an existing `top`, so `gh` is reached) with a `run` that throws `Object.assign(Error("x"), { code: "ENOENT" })` for `status` and a timeout-style error (`killed: true`) for `gh`: `got.errors` contains `{ step: "status", code: "ENOENT" }` and `{ step: "pr", code: "timeout" }`, and no message text; (b) in the titles tests, after `h.end()` the stored record has `diag` with `instance` a non-empty string, `branch: "feat/x"`, `upstream: true`, `errors: []`; with `facts.fail = true` the record's `diag.errors` is non-empty and contains no `"run"` message text; (c) a stored record with a malformed `diag` (`{ instance: 5 }`, extra keys, `errors` not an array) loads without breaking the session and the bad `diag` is replaced at the next save; (d) an unchanged second `h.end()` writes nothing (`storage.calls.set` unchanged).
- [ ] **Step 2:** Run both test files; expect tests (a) to (c) to FAIL (test (d) already passes and guards the dedupe).
- [ ] **Step 3:** In `readFacts`, push `{ step, code }` where each read now catches (`status`, `diff`, `plan`, `pr`); `code` = `"timeout"` when `error.killed`, else `error.code` when a string, `"exit:<n>"` when `error.code` is an integer (execFile reports the exit code there), else `"error"`. Add `errors: []` to the default facts.
- [ ] **Step 4:** In `session-titles.js`, create an `instance` id once per `registerSessionTitles` call; set `entry.diag` inside the turn-end read, right after `readFacts` (a turn start reads no facts), with `declared` and `done` counts; include it in `save`'s record and snapshot; in `load`, accept `record.diag` only when it matches the shape above with bounds (`step` in the four names, `code` matching `/^[\w:]{1,40}$/`, at most 5 errors, strings at most 200 characters, integers for the counts, no extra keys), else `null`.
- [ ] **Step 5:** Run both files plus `node --test tests/*.test.js`; expect PASS. Commit `feat: store what the title meter last read and which read failed`.

### Task 3: Record the branch at push, PR create and PR merge

**Files:** Modify `session-facts.js` (add `branchOf`), `session-titles.js` (the tool-hook handler), `tests/session-facts.test.js`, `tests/session-titles.test.js`

**Interfaces:**
- Produces: `branchOf({ top, run = defaultRun }): Promise<string | null>` in `session-facts.js`; the branch checked out in `top`, or null when detached, equal to the default branch (`origin/HEAD`, fallback `main`), or unreadable.
- Consumes: `classify(command)` from `pr-advice.js` (kinds `push`, `pr_create`, `pr_merge`).

- [ ] **Step 1:** Add tests. Facts: `branchOf` against the `setup(t)` repo returns null detached, `"feat/x"` on a branch with no upstream, null on `main`. Titles (helper `shell(command, sessionID, more)` that emits an `execute.after` event `{ tool: "shell", status: "completed", sessionID, input: { command } }` like `write`): (a) bound session, `facts.branch = "feat/x"`, `facts.upstream = false`, `await h.shell("git push origin HEAD")` stores `task.remembered === "feat/x"` in the record; (b) the one-turn flow: bind, `h.shell("gh pr merge 5 --merge")`, remove the root with `rmSync(h.root, { recursive: true, force: true })`, set `h.facts.prs = { "feat/x": [{ state: "MERGED", title: "x" }] }`, `await h.end()`, expect `compose("Fix login flow", 5, false)`; the same without the shell call stays below 5; (c) an unbound session, a stored child session (seed a record for `child_1`, whose `session.get` carries `parentID`) and `status: "error"` write nothing and do not run `git status`; (d) `facts.branch = "main"` and a detached branch record nothing; (e) feature off (`state: "off"`) does no work; (f) `git status` throwing leaves the entry unchanged and does not throw.
- [ ] **Step 2:** Run both files; expect `branchOf` and tests 3(a) and 3(b) to FAIL; (c) to (f) guard against regressions and pass before the fix.
- [ ] **Step 3:** Implement `branchOf` as its own function: `git status --porcelain=v2 --branch -uno` for the branch and `git rev-parse --abbrev-ref origin/HEAD` for the default (fallback `origin/main`); no shared helper. In the handler, for `event.tool` in `shell`/`bash` with `status === "completed"`, a string `sessionID` and `input.command`, `classify` it; if any action kind is `push`, `pr_create` or `pr_merge`, `enqueue` a job that checks, in this order, `on()`, then `load(sessionID)`, then a bound unsilenced task, then that `ctx.session.get` carries no `parentID`, calls `branchOf({ top: task.top, run })`, and on a branch sets `task.remembered` and saves.
- [ ] **Step 4:** Run `node --test tests/*.test.js`, `python3 -B -m unittest discover -s tests`, `python3 -B scripts/validate.py`; expect all pass.
- [ ] **Step 5:** Commit `fix: record the task's branch when it is pushed, opened or merged`.
