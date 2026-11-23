# Title Sync: WAITING Only When Nothing Is Running — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The title shows `WAITING` at a root turn end only when no descendant session of that root is running, and shows it once the last descendant ends while the root stays idle.

**Architecture:** `src/session-titles.js` gains two small helpers, `rootOf` (walk `parentID` up) and `activity` (read the host's active sessions and decide whether a descendant of a root runs). `evaluate` uses `activity` for the label, and the event loop queues one more root evaluation when a session with a parent ends.

**Tech Stack:** Node ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-23-title-waiting-idle-design.md`

> **Superseded in part:** the `ctx.session.active()` text in Global Constraints, Review Focus and Task 1, and "a grandchild counts", were replaced by the Amendment at the end of this file (and by the spec's Amendment): the real host has no `active()`, and only direct children count.

## Global Constraints

- Standard library only; advisory only; the title is not written at tool time.
- A missing, failing or oddly shaped `ctx.session.active()` counts as "nothing running" (today's behavior).
- At most 5 parent steps from a session to the root; deeper sessions are ignored.
- No README, onboarding or injected-note change; the live injection check is not triggered.
- Tests live in `tests/plugin/session-titles.test.js`; run `node --test "tests/**/*.test.js"`, `python3 -B -m unittest discover -s tests -t .`, `python3 -B scripts/validate.py`.

## Review Focus

- A burst of children ending: only the last one to end may write the label (Task 2 test `two children`).
- The root is active again because a child's report woke it: nothing is written (Task 2 test `root active`).
- `active()` returns `{ data: { <id>: { type: "running" } } }` or the bare map: both work (Task 1 test `result shapes`).
- An unbound or silenced root: a child's end does no git or gh read and no rename (Task 2 tests `unbound root`, `silenced root`).
- A grandchild counts, a child of another root does not, the root itself does not, and a chain deeper than 5 steps does not (Task 1 tests).

---

### Task 1: Decide the label from the running descendants

**Files:**
- Modify: `src/session-titles.js` (`evaluate`, new `rootOf` and `activity`, constant `MAX_HOPS = 5`)
- Test: `tests/plugin/session-titles.test.js` (the `host` fake and new tests after `a turn start during a slow evaluation ends without WAITING`)

**Interfaces:**
- Produces: `rootOf(sessionID: string): Promise<string | null>` — follows `ctx.session.get({ sessionID }).parentID` up to `MAX_HOPS` steps and returns the id of the session with no parent; `null` on an error or when the limit is passed. A session with no parent returns itself.
- Produces: `activity(rootID: string): Promise<{ children: boolean, self: boolean }>` — `self` is whether `rootID` is in the active list; `children` is whether any other active session reaches `rootID` through `rootOf`-style steps within `MAX_HOPS`. Any failure returns `{ children: false, self: false }`.
- Consumes: `ctx.session.active()`; accept the bare map `{ <id>: { type: "running" } }` and the wrapper `{ data: <map> }`; ignore anything else.

- [ ] **Step 1: Extend the `host` fake and write the failing tests.** In `host`, add to `facts`: `active: []`, `parents: {}`, `activeFail: false`, `activeWrap: false`, `activeRaw: undefined`; add `active: 0` to `calls`; add `ctx.session.active` that increments `calls.active`, throws when `facts.activeFail`, returns `facts.activeRaw` when it is not `undefined`, else the map of `facts.active` ids to `{ type: "running" }` (inside `{ data }` when `facts.activeWrap`). Make `ctx.session.get` return `parentID: facts.parents[sessionID] ?? (sessionID.startsWith("child") ? "ses_1" : undefined)`. Then add tests (each binds with `await h.write(SPEC)` and uses `h.end()`):
  - `a root turn that ends while a child runs shows the meter without WAITING`: `facts.active = ["child_1"]`, end → `titles.get("ses_1") === compose("Fix login flow", 1, false)`; `facts.active = []`, end again → `compose("Fix login flow", 1, true)`.
  - `a grandchild counts; another root's child, the root itself and a chain past 5 steps do not`: with `parents = { grand_1: "child_1" }` active `["grand_1"]` → no label. With `parents = { child_9: "ses_2" }` active `["child_9"]` → label. Active `["ses_1"]` → label. With `parents = { d1: "ses_1", d2: "d1", d3: "d2", d4: "d3", d5: "d4", d6: "d5" }`: active `["d5"]` → no label, active `["d6"]` → label.
  - `active result shapes and failures`: `activeWrap = true` with `["child_1"]` → no label; `activeFail = true`, `activeRaw = null`, `activeRaw = "x"`, `activeRaw = { data: 5 }` each → label (today's behavior).
  - `only a root turn end reads the active sessions`: `h.start()`, a write, an unbound session's end and a parentless session's end leave `h.calls.active === 0`.
- [ ] **Step 2: Run `node --test tests/plugin/session-titles.test.js`.** Expected: the first three new tests FAIL (label shown while a child runs); the fourth passes already and guards the reads.
- [ ] **Step 3: Implement `rootOf` and `activity` as above, and in `evaluate` read `activity(sessionID)` once after the facts read and before `compose`**, so the label is `ended && cells < 5 && !activity.children`. Reuse `ctx.session.get`; no cache.
- [ ] **Step 4: Run the file, then `node --test "tests/**/*.test.js"`.** Expected: all pass (244 before, plus the new tests).
- [ ] **Step 5: Commit** `feat: show WAITING only when no descendant session is running`.

### Task 2: Re-evaluate the root when a child session ends

**Files:**
- Modify: `src/session-titles.js` (`evaluate` gains a third parameter `fromChild = false`; the event loop), `docs/superpowers/specs/2026-11-20-session-titles-design.md` ("The `WAITING` label" and the accepted-gap bullet)
- Test: `tests/plugin/session-titles.test.js`

**Interfaces:**
- Consumes: `rootOf`, `activity` from Task 1.
- Produces: `evaluate(sessionID: string, ended: boolean, fromChild?: boolean): Promise<void>`. With `fromChild` it returns before any facts read when `activity(sessionID)` reports `children` or `self`; otherwise it runs the normal end-of-turn evaluation.

- [ ] **Step 1: Write the failing tests** (a bound root `ses_1`, `parents` as in Task 1; `h.end("child_1")` ends the child):
  - `the label appears when the last running child ends and the root is idle`: active `["child_1"]`, root end → no label; active `[]`, `await h.end("child_1")` → `compose("Fix login flow", 1, true)`.
  - `two children`: active `["child_1", "child_2"]` with `parents.child_2 = "ses_1"`; root end → no label; active `["child_2"]`, end `child_1` → `h.calls.update.length` unchanged; active `[]`, end `child_2` → label.
  - `root active`: active `["ses_1"]`, end `child_1` → `h.calls.update.length` unchanged and no `gh` call.
  - `failed and interrupted children count`: `h.emit("session.execution.failed", "child_1")` and `...interrupted...` each produce the label when nothing runs.
  - `unbound root`: no `h.write`; end `child_1` → `h.calls.update` is `[]` and `h.calls.run` is `[]`.
  - `silenced root`: after a label, `h.titles.set("ses_1", "My own title")`, end `child_1` → the title stays `My own title` and `h.calls.update.length` is unchanged.
- [ ] **Step 2: Run the file.** Expected: `label appears`, `two children` (last step) and `failed and interrupted` FAIL; `root active`, `unbound root` and `silenced root` already pass and guard regressions.
- [ ] **Step 3: Implement.** In the event loop, for every event that is not a start, also start a detached job: `rootOf(sessionID)`, and when the result is a different id, `enqueue(root, () => evaluate(root, true, true))`; failures are swallowed. In `evaluate`, when `fromChild`, run `activity(sessionID)` right after the existing guards and return when `children || self`.
- [ ] **Step 4: Update the 2026-11-20 design.** Replace the accepted-gap sentence under "The `WAITING` label" with one that says `WAITING` shows only while no descendant session runs and links `2026-11-23-title-waiting-idle-design.md`; remove or amend the matching "Known gaps" line if present.
- [ ] **Step 5: Run `node --test "tests/**/*.test.js"`, `python3 -B -m unittest discover -s tests -t .` and `python3 -B scripts/validate.py`.** Expected: all pass.
- [ ] **Step 6: Commit** `feat: re-evaluate the root session when a child session ends`.

## Execution and what is left

- Execution method: Native (two tasks in one file with shared helpers; a fresh reviewer for the whole branch afterwards).
- After Task 2: an independent review of the branch, then the live check described in the spec's "Proof expected" (needs the user's approval for inference), then a pull request.

## Amendment, 2026-11-23 (after the first live run)

The live run showed that the real plugin context has no `ctx.session.active()`. `activity` now lists the root's
children with `ctx.session.list({ parentID, limit: 50, order: "desc" })` and reads `Session.Info.time` (`busy`,
`idle`, `updated`); `evaluate`'s third parameter is the id of the session that ended (or `null`). Only direct
children count. See the amendment in the spec for the verified host facts and the reasons.
