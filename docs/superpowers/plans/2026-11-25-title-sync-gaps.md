# Title Sync Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close six confirmed gaps between session titles and the chat: one owner per session, waiting requests, the server-start running rule, background shells, the seen branch, and subagent/plan binding.

**Architecture:** All behavior lives in `src/session-titles.js` (ownership, events, labels) and `src/session-facts.js` (git and `gh` facts). New events are subscribed on the existing stream; no timers or background reads.

**Tech Stack:** Node ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-25-title-sync-gaps-design.md` (the authority; read it first).

## Global Constraints

- Standard library only; advisory only (git reads, never writes); tests first; no README, skill, onboarding or note change.
- Existing guarantees stay green: one queue per session, nothing new written after close or after a move, an unchanged turn writes nothing to storage, no title write at bind time, children never get their own meter, cells never go down.
- Caps: 16 pending requests per session (17th ignored), 64 tracked shells (65th drops the oldest), 64 remembered exited shell ids, 256 generations.
- Server start: `Date.now() - process.uptime() * 1000`, no slack; tests inject it with a `started` option.
- Commands: `node --test "tests/**/*.test.js"`, `python3 -B -m unittest discover -s tests -t .`, `python3 -B scripts/validate.py`.

## Review Focus

- A session moved back and forth between two loaded copies keeps its plan and cells (Task 3, `the lab move sequence keeps the plan`).
- A stray or duplicate reply never removes an idle session's `WAITING` (Task 4, `an unmatched reply leaves an idle WAITING`).
- A failed `stat` or `git status` never credits a merged PR through `seen` (Task 2, `a failed read holds the cells`).
- A plan with another topic, or a second plan, never moves the task (Task 6, `only the first same-topic plan adopts`).
- A shell that exits before its hook runs is never tracked as running (Task 5, `an exit before the hook`).

---

### Task 1: Running means busy since this server started

**Files:** Modify `src/session-titles.js` (`activity`, remove `STALE_MS`, new option `started`); Test `tests/plugin/session-titles.test.js`.

**Interfaces:**
- Produces: `registerSessionTitles(ctx, { ..., started? })` where `started` defaults to `Date.now() - process.uptime() * 1000`; an internal `running(time)` used by `activity` for children and the root.

- [ ] **Step 1:** Add `started` to the `host` fake options (passed through) and write tests: `a child busy for 11 minutes still counts as running` (started 20 min ago, child busy 11 min ago, no idle → no `WAITING`); `a busy time 1 ms before the server start reads idle and 1 ms after reads running` (for a child at a root turn end, and for the root in the child-triggered path); `two copies registered at different times compute the same start` (default option, two registrations, compare through behavior or an exported helper `serverStart()`). Rename existing age-rule test names that mention 10 minutes.
- [ ] **Step 2:** Run `node --test tests/plugin/session-titles.test.js`; expected: the 11-minute and boundary tests FAIL.
- [ ] **Step 3:** Implement; delete `STALE_MS` and the `time.updated` read.
- [ ] **Step 4:** Run the file, then all Node tests; expected: PASS.
- [ ] **Step 5:** Commit `fix: a session runs when it became busy after this server started`.

### Task 2: Facts report removal, detachment and dated PRs

**Files:** Modify `src/session-facts.js` (`readFacts`, new `commonDir`); Test `tests/plugin/session-facts.test.js`.

**Interfaces:**
- Produces: `readFacts({ top, plan, remembered, seen = null, bound = null, fallback, run })` → adds `gone: boolean` (stat failed with `ENOENT`), `detached: boolean` (successful status read shows a detached head), `via: "branch" | "remembered" | "seen" | null`; the `gh` call asks `--json state,title,createdAt`; a PR found via `seen` is kept only when `bound` is a number and `Date.parse(createdAt) >= bound`.
- Produces: `commonDir({ top, run }) → Promise<string | null>` (`git rev-parse --path-format=absolute --git-common-dir`, trimmed; null on failure).

- [ ] **Step 1:** Tests (real git repo from `setup(t)` plus a `gh` recorder): `seen is used only when the checkout is gone or detached` (existing unpushed branch: no `gh` call; removed folder: lookup by seen; detached: lookup by seen); `a failed read holds the cells` (status throws → no `seen` lookup; stat failing with a non-ENOENT error → `gone: false`, no lookup); `a PR created before the bind does not count through seen` (createdAt 30 s before `bound` → `pr: null`; at or after → kept; no `bound` → seen unused); `remembered still wins over seen and needs no date`; `commonDir is equal for a checkout and its linked worktree and null on failure`.
- [ ] **Step 2:** Run `node --test tests/plugin/session-facts.test.js`; expected: new tests FAIL.
- [ ] **Step 3:** Implement as specified in design §5; keep existing error codes in `errors`.
- [ ] **Step 4:** Run the file and all Node tests; expected: PASS.
- [ ] **Step 5:** Commit `feat: facts tell a removed or detached checkout and date the PR`.

### Task 3: One owner per session, moves and subagent binds

**Files:** Modify `src/session-titles.js` (`load`, `save`, `bind`, `record`, `evaluate`, `rootOf` → returns the root's session info, `enqueue` passes the admitted generation, new `owns`, `forget`, `session.moved` handling); Test `tests/plugin/session-titles.test.js` (new `world` fake).

**Interfaces:**
- Consumes: none from earlier tasks.
- Produces: `enqueue(sessionID, job)` calls `job(admitted)` with the generation noted at queue time; `forget(sessionID)`; `owns(info) → true | false | undefined` (undefined = unknown); stored records gain `project`; tasks gain `seen: null`, `bound: number` (set at bind); `restore` accepts both (missing → null).
- Test helper `world(t, { folders: ["main","wt"], project: "p1", ... })`: one shared storage, one shared session table (`{ id → { location, title, parentID, time } }`), one shared `run` fake; per folder a `registerSessionTitles` copy whose `ctx.location = { directory, project: { id } }`; `push(event)` reaches every copy's stream; `write(path, sid)` / `shell(cmd, sid)` call the hook of the copy whose folder equals the session's location; `move(sid, folder)` changes the location and pushes `{ type: "session.moved", data: { sessionID, location: { directory } } }`; per-copy call counters (gets, `gh`, storage sets).

- [ ] **Step 1:** Tests: `the lab move sequence keeps the plan` (bind spec from main into wt path, end; move to wt; plan + changed file, end; move back; end → plan kept, cells not lower, one owner wrote each title); `a record from another project is ignored and a legacy record loads only in its folder`; `a failed ownership read retries on the next event`; `a non-owner makes one cold ownership read and no gh call or storage write`; `a move during a slow evaluation or a slow storage read writes nothing in the old copy`; `a close during a slow evaluation starts no new write`; `the move's turn end is evaluated once, by the destination`; `a child end first after a reload restores and wakes its stored root`; `a child's spec write binds the root through the root's queue` and `a child in another folder does not bind the root`. Update `a record saved by another project's instance is ignored` to the project rule.
- [ ] **Step 2:** Run the file; expected: the move, project, non-owner and child-bind tests FAIL.
- [ ] **Step 3:** Implement design §1 and §6.
- [ ] **Step 4:** Run the file and all Node tests; expected: PASS (all earlier tests, including `an unchanged turn writes nothing to storage`).
- [ ] **Step 5:** Commit `fix: one plugin copy owns a session; moves never bring back older state`.

### Task 4: WAITING for pending permission requests and forms

**Files:** Modify `src/session-titles.js` (subscriptions, `pending`, `relabel`); Test `tests/plugin/session-titles.test.js`.

**Interfaces:**
- Consumes: `owns`, admitted generations (Task 3); `running(time)` (Task 1).
- Produces: `relabel(sessionID, waiting, admitted, { onlyIfRunning })` (no git or `gh` reads).

- [ ] **Step 1:** Extend the `host` fake's subscription check (new types listed) and write tests: `a permission request sets WAITING mid-turn without git reads and its reply removes it`; `a question form does the same, and cancel removes it`; `overlapping requests keep WAITING until the last answer`; `an unmatched reply leaves an idle WAITING`; `a dropped reply keeps WAITING until the root's next boundary`; `no request label before the first meter or for a task bound this turn`; `a child-triggered evaluation does not clear pending requests`; `a 17th request is ignored`; `requests of unbound or child sessions do nothing`.
- [ ] **Step 2:** Run; expected: FAIL.
- [ ] **Step 3:** Implement design §2.
- [ ] **Step 4:** Run the file and all Node tests; expected: PASS.
- [ ] **Step 5:** Commit `feat: show WAITING while a permission request or question waits mid-turn`.

### Task 5: Background shells hold the label

**Files:** Modify `src/session-titles.js` (hook branch for shell results, `shell.exited`, turn-end label); Test `tests/plugin/session-titles.test.js`.

**Interfaces:**
- Consumes: `relabel`, `activity`, admitted generations.

- [ ] **Step 1:** Tests (the `shell` helper gains a `result` field): `a running background shell keeps WAITING off at a turn end`; `its exit with nothing running sets WAITING at once`; `its exit while the session runs again writes nothing`; `two shells: only the last exit labels`; `an exit before the hook`; `a 65th shell drops the oldest`; `a background git push still records its branch`.
- [ ] **Step 2:** Run; expected: FAIL.
- [ ] **Step 3:** Implement design §4.
- [ ] **Step 4:** Run the file and all Node tests; expected: PASS.
- [ ] **Step 5:** Commit `feat: a running background shell keeps WAITING off`.

### Task 6: The seen branch and plan adoption

**Files:** Modify `src/session-titles.js` (`evaluate` sets `task.seen`, passes `seen`/`bound` to `readFacts`; `bind` adoption); Test `tests/plugin/session-titles.test.js` (the fake `run` answers `rev-parse --git-common-dir` and returns `createdAt` in PRs).

**Interfaces:**
- Consumes: `readFacts` with `seen`, `bound`, `gone`, `detached`; `commonDir` (Task 2).

- [ ] **Step 1:** Tests: `a removed worktree with a seen branch reaches 5 cells`; `with cleanup on, a detached present checkout shows 4 and 5 once removed`; `an unpushed existing checkout makes no gh call`; `only the first same-topic plan adopts` (adopts and resets `remembered`/`seen`, also after the main checkout switched branch; a different topic, a second plan, another repository or a failed `commonDir` read does not).
- [ ] **Step 2:** Run; expected: FAIL.
- [ ] **Step 3:** Implement design §5 (title side) and §7.
- [ ] **Step 4:** Run all three commands from Global Constraints; expected: PASS.
- [ ] **Step 5:** Commit `feat: remember the seen branch and let the first matching plan adopt the task`.

## After the tasks

- Final whole-branch review by a fresh reviewer, fix pass for Critical/Important.
- Live re-qualification in the lab (`scratchpad/qualify`): rerun A1, B1, D1, D2, E1, F1-child and E2 against the changed plugin; each must show the expected title. Budget: at most 12 `claude-agent/haiku` low prompts plus the 13-minute E2 run.
- Pull request per the project's git workflow.
